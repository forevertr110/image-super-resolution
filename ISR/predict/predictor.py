import argparse
import imageio
import yaml
import numpy as np
from pathlib import Path
from time import time
from ISR.model import RDN
from ISR.utils.logger import get_logger
from ISR.utils.utils import get_timestamp
from ISR.utils.image_processing import process_array, process_output
from ISR.utils.image_processing import split_image_into_overlapping_patches, stich_together

parser = argparse.ArgumentParser(
    description='Resize a given image',
    formatter_class=argparse.ArgumentDefaultsHelpFormatter)

parser.add_argument('--input', metavar='PATH', type=str, required=True, help='Input file or directory')
parser.add_argument('--output', metavar='PATH', type=str, default="data/output", help='Output directory')
parser.add_argument('--model_name', metavar='MODEL', type=str, default='weights/rdn-C6-D20-G64-G064-x2_ArtefactCancelling_epoch219.hdf5', help='Pretrained model name')

parser.add_argument('--batch_size', metavar='BATCH', type=int, default=10, help='Batch size')
parser.add_argument('--patch_size', metavar='PATCH', type=int, default=0, help='Patch size')

parser.add_argument('--verbose', metavar='PATCH', type=bool, default=TRUE, help='Verbosity')

def main():
    args = parser.parse_args()
    pred = Predictor(args.input, args.output, args.verbose)
    if args.model_name == "weights/rdn-C6-D20-G64-G064-x2_ArtefactCancelling_epoch219.hdf5":
        rdn = RDN(arch_params={'C': 6, 'D':20, 'G':64, 'G0':64, 'x':2})
    else:
        self.logger.error("Model {} does not exist!".format(args.model_name))
        raise ValueError("Model {} does not exist!".format(args.model_name))
    
    pred.get_predictions(model=rdn, weights_path=args.model_name)


class Predictor:
    """The predictor class handles prediction, given an input model.

    Loads the images in the input directory, executes training given a model
    and saves the results in the output directory.
    Can receive a path for the weights or can let the user browse through the
    weights directory for the desired weights.

    Args:
        input_dir: string, path to the input directory.
        output_dir: string, path to the output directory.
        verbose: bool.

    Attributes:
        extensions: list of accepted image extensions.
        img_ls: list of image files in input_dir.

    Methods:
        get_predictions: given a model and a string containing the weights' path,
            runs the predictions on the images contained in the input directory and
            stores the results in the output directory.
    """

    def __init__(self, input_dir, output_dir='./data/output', verbose=True):

        self.input_dir = Path(input_dir)
        self.data_name = self.input_dir.name
        self.output_dir = Path(output_dir) / self.data_name
        self.logger = get_logger(__name__)
        if not verbose:
            self.logger.setLevel(40)
        self.extensions = ('.jpeg', '.jpg', '.png')  # file extensions that are admitted
        self.img_ls = [f for f in self.input_dir.iterdir() if f.suffix in self.extensions]
        if len(self.img_ls) < 1:
            self.logger.error('No valid image files found (check config file).')
            raise ValueError('No valid image files found (check config file).')
        # Create results folder
        if not self.output_dir.exists():
            self.logger.info('Creating output directory:\n{}'.format(self.output_dir))
            self.output_dir.mkdir(parents=True)

    def _load_weights(self):
        """ Invokes the model's load weights function if any weights are provided. """
        if self.weights_path is not None:
            self.logger.info('Loaded weights from \n > {}'.format(self.weights_path))
            # loading by name automatically excludes the vgg layers
            self.model.model.load_weights(self.weights_path)
        else:
            self.logger.error('Error: Weights path not specified (check config file).')
            raise ValueError('Weights path not specified (check config file).')

        session_config_path = self.weights_path.parent / 'session_config.yml'
        if session_config_path.exists():
            conf = yaml.load(session_config_path.read_text(), Loader=yaml.FullLoader)
        else:
            self.logger.warning('Could not find weights training configuration')
            conf = {}
        conf.update({'pre-trained-weights': self.weights_path.name})
        return conf

    def _make_basename(self):
        """ Combines generators's name and its architecture's parameters. """

        params = [self.model.name]
        for param in np.sort(list(self.model.params.keys())):
            params.append('{g}{p}'.format(g=param, p=self.model.params[param]))
        return '-'.join(params)

    def get_predictions(self, model, weights_path):
        """ Runs the prediction. """

        self.model = model
        self.weights_path = Path(weights_path)
        weights_conf = self._load_weights()
        out_folder = self.output_dir / self._make_basename() / get_timestamp()
        self.logger.info('Results in:\n > {}'.format(out_folder))
        if out_folder.exists():
            self.logger.warning('Directory exists, might overwrite files')
        else:
            out_folder.mkdir(parents=True)
        if weights_conf:
            yaml.dump(weights_conf, (out_folder / 'weights_config.yml').open('w'))
        # Predict and store
        for img_path in self.img_ls:
            output_path = out_folder / img_path.name
            self.logger.info('Processing file\n > {}'.format(img_path))
            start = time()
            sr_img = self._forward_pass(img_path)
            end = time()
            self.logger.info('Elapsed time: {}s'.format(end - start))
            self.logger.info('Result in: {}'.format(output_path))
            imageio.imwrite(output_path, sr_img)

    def _forward_pass(self, file_path):
        lr_img = imageio.imread(file_path)
        if lr_img.shape[2] == 3:
            if lr_img.size[1] > 1024:
                sr_img = self._predict(model,lr_img,256)
            else:
                sr_img = self._predict(model,lr_img)
            return sr_img
        else:
            self.logger.error('{} is not an image with 3 channels.'.format(file_path))
            
    def _predict(model, input_image_array, by_patch_of_size=None, batch_size=10, padding_size=2):
        """
        Processes the image array into a suitable format
        and transforms the network output in a suitable image format.

        Args:
            input_image_array: input image array.
            by_patch_of_size: for large image inference. Splits the image into
                patches of the given size.
            padding_size: for large image inference. Padding between the patches.
                Increase the value if there is seamlines.
            batch_size: for large image inferce. Number of patches processed at a time.
                Keep low and increase by_patch_of_size instead.
        Returns:
            sr_img: image output.
        """

        if by_patch_of_size:
            self.logger.info("Patches of size {}".format(by_patch_of_size))
            lr_img = process_array(input_image_array, expand=False)
            patches, p_shape = split_image_into_overlapping_patches(
                lr_img, patch_size=by_patch_of_size, padding_size=padding_size
            )
            # return patches
            self.logger.info("No. patches: {}".format(len(patches)))
            for i in range(0, len(patches), batch_size):
                self.logger.info("Processing patch: {}/{}".format(i,len(patches)))
                batch = model.predict(patches[i : i + batch_size])
                if i == 0:
                    collect = batch
                else:
                    collect = np.append(collect, batch, axis=0)
                    
                scale = model.scale
                padded_size_scaled = tuple(np.multiply(p_shape[0:2], scale)) + (3,)
                scaled_image_shape = tuple(np.multiply(input_image_array.shape[0:2], scale)) + (3,)
                sr_img = stich_together(
                    collect,
                    padded_image_shape=padded_size_scaled,
                    target_shape=scaled_image_shape,
                    padding_size=padding_size * scale,
                )
                sr_img
                process_output(sr_img)

            scale = model.scale
            padded_size_scaled = tuple(np.multiply(p_shape[0:2], scale)) + (3,)
            scaled_image_shape = tuple(np.multiply(input_image_array.shape[0:2], scale)) + (3,)
            sr_img = stich_together(
                collect,
                padded_image_shape=padded_size_scaled,
                target_shape=scaled_image_shape,
                padding_size=padding_size * scale,
            )

        else:
            lr_img = process_array(input_image_array)
            sr_img = model.predict(lr_img)[0]

        sr_img = process_output(sr_img)
        return sr_img


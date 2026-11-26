import json
import os
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / 'flower_cbir/config/pipeline.json'


def relative_project_path(path):
    """Serialize paths relative to the repository, independently of the working directory."""
    resolved = (PROJECT_ROOT / path).resolve()
    return Path(os.path.relpath(resolved, PROJECT_ROOT)).as_posix()


def load_config():
    config = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    if config['image_size'] != [384, 384] or config['features']['total_dims'] != 163:
        raise ValueError('Pipeline requires 384x384 images and the 163D feature schema.')
    if config['geometry'] != {'b5_kernel_shape': 'ellipse', 'b5_kernel_size': [5, 5], 'erode_iterations': 1}:
        raise ValueError('The report fixes erosion to one 5x5 ellipse with zero outside the image.')
    if config['epsilon'] != 1e-12:
        raise ValueError('The report fixes epsilon to 1e-12.')
    validate_weights(tuple(config['distance']['weights'].values()))
    return config


def validate_weights(weights):
    values = np.asarray(weights, dtype=np.float64)
    if values.shape != (4,) or not np.isfinite(values).all() or np.any(values < 0) or values.sum() <= 0:
        raise ValueError('Weights must contain four finite nonnegative values with positive sum.')
    return tuple((values / values.sum()).tolist())


def default_weights():
    config = load_config()
    return validate_weights([config['distance']['weights'][f'w{index}'] for index in range(1, 5)])

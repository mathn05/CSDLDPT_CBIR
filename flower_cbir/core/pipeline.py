"""The only image -> mask -> 163D entry point, for batch import and queries."""
import time

import numpy as np

from flower_cbir.core.deeplab import run_deeplab
from flower_cbir.core.features import extract_all_features
from flower_cbir.core.preprocess import load_image
from flower_cbir.core.segmentation import clean_mask, create_mask_overlay
from flower_cbir.settings import load_config


def extract_image(source, supplied_mask=None):
    timings = {}
    started = time.perf_counter()
    rgb = load_image(source)
    mask = supplied_mask
    timings['decode_ms'] = (time.perf_counter()-started)*1000
    started = time.perf_counter()
    if mask is None:
        mask, overlay, model_info = run_deeplab(rgb)
        segmentation = {'method': load_config()['segmentation']['method'], **model_info}
    else:
        if np.shape(mask) != rgb.shape[:2]:
            raise ValueError("Mask dimensions must match the input image.")
        mask = clean_mask(np.asarray(mask))
        overlay = create_mask_overlay(rgb, mask)
        segmentation = {'method': 'supplied_mask'}
    timings['segmentation_ms'] = (time.perf_counter()-started)*1000
    started = time.perf_counter()
    vector, explanation = extract_all_features(rgb, mask)
    timings['extraction_ms'] = (time.perf_counter()-started)*1000
    return {'image_rgb': rgb, 'mask_binary': mask, 'overlay': overlay,
            'raw_vector': vector, 'explanation': explanation,
            'segmentation': segmentation, 'timings': timings}

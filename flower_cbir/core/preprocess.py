import io
from pathlib import Path

import numpy as np
from PIL import Image


def load_image(source):
    """Decode the input at its original dimensions."""
    if isinstance(source, np.ndarray):
        if source.dtype == np.uint8 and source.shape == (384, 384, 3):
            return source
        if source.dtype != np.uint8 or source.ndim not in (2, 3):
            raise ValueError("Expected a uint8 image.")
        source = Image.fromarray(source)
    if isinstance(source, bytes):
        source = io.BytesIO(source)
    if isinstance(source, (str, Path, io.BytesIO)):
        with Image.open(source) as image:
            return load_image(image)
    if not isinstance(source, Image.Image):
        raise ValueError(f"Unsupported image input type: {type(source)}")
    if source.size != (384, 384):
        raise ValueError("Ảnh đầu vào phải có kích thước 384 × 384 px.")
    return np.array(source if source.mode == "RGB" else source.convert("RGB"))

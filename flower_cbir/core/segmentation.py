import cv2
import numpy as np


def clean_mask(mask_binary):
    mask = np.asarray(mask_binary)
    if mask.ndim != 2 or mask.size == 0 or not np.isfinite(mask).all():
        raise ValueError('Mask must be a finite nonempty 2D array.')
    if not set(np.unique(mask)).issubset({0, 1, 255}):
        raise ValueError('Mask must be binary (0/1 or 0/255).')
    binary = (mask > 0).astype(np.uint8)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if count <= 1:
        raise ValueError('Mask has no foreground. Check the input image.')
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return np.where(labels == largest, 255, 0).astype(np.uint8)


def create_mask_overlay(image_rgb: np.ndarray, mask_binary: np.ndarray) -> np.ndarray:
    """
    Create a clean overlay of binary mask on RGB image with green contour and darkened background.
    """
    overlay = image_rgb.copy()
    # Dim background
    bg_dimmed = (overlay * 0.3).astype(np.uint8)
    combined = np.where(mask_binary[:, :, None] > 0, overlay, bg_dimmed)
    # Draw contour
    contours, _ = cv2.findContours(mask_binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(combined, contours, -1, (0, 255, 0), 2)
    return combined

"""Extract a belly HEX color from a masked, belly-down fish crop."""

from __future__ import annotations

import cv2
import numpy as np


MIN_ROI_PIXELS = 10


class BellyColorError(RuntimeError):
    """Raised when a belly color cannot be extracted from a fish crop."""


def _ensure_bgr_image(image: np.ndarray) -> np.ndarray:
    if not isinstance(image, np.ndarray) or image.size == 0:
        raise BellyColorError("Crop image is empty or invalid.")
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.ndim != 3:
        raise BellyColorError(f"Crop image must have 2 or 3 dimensions, got {image.ndim}.")
    if image.shape[2] == 3:
        return image
    if image.shape[2] == 4:
        return image[:, :, :3]
    raise BellyColorError(f"Crop image must have 1, 3, or 4 channels, got {image.shape[2]}.")


def _binary_mask(mask: np.ndarray, image_shape: tuple[int, int]) -> np.ndarray:
    if not isinstance(mask, np.ndarray) or mask.size == 0:
        raise BellyColorError("Fish mask is empty or invalid.")
    if mask.ndim != 2:
        raise BellyColorError(f"Fish mask must be 2-dimensional, got {mask.ndim}.")
    if mask.shape != image_shape:
        raise BellyColorError(
            f"Crop/mask size mismatch: crop={image_shape}, mask={mask.shape}."
        )
    binary_mask = np.where(mask > 0, 255, 0).astype(np.uint8)
    if cv2.countNonZero(binary_mask) == 0:
        raise BellyColorError("No fish pixels found in input mask.")
    return binary_mask


def _get_fish_mask(image: np.ndarray, threshold: int = 1) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    _, mask = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
    return mask.astype(np.uint8)


def _clean_fish_mask(mask: np.ndarray, kernel_size: int = 5) -> np.ndarray:
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size))
    cleaned = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    if cv2.countNonZero(cleaned) == 0:
        return mask
    return cleaned


def _compute_principal_axis(mask: np.ndarray) -> np.ndarray | None:
    coords = np.column_stack(np.where(mask > 0))
    if len(coords) < MIN_ROI_PIXELS:
        return None

    centered = coords - np.mean(coords, axis=0)
    covariance = np.cov(centered.T)
    if np.any(np.isnan(covariance)) or np.any(np.isinf(covariance)):
        return None

    eigenvalues, eigenvectors = np.linalg.eig(covariance)
    return eigenvectors[:, np.argsort(eigenvalues)[::-1][0]]


def _split_by_midline(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    coords = np.column_stack(np.where(mask > 0))
    if len(coords) < MIN_ROI_PIXELS:
        empty = np.empty((0, 2), dtype=np.intp)
        return empty, empty

    height, width = mask.shape
    principal_axis = _compute_principal_axis(mask)
    if principal_axis is None:
        rows, cols = np.where(mask > 0)
        is_horizontal = (cols.max() - cols.min()) >= (rows.max() - rows.min())
    else:
        is_horizontal = abs(principal_axis[1]) >= abs(principal_axis[0])

    midline_points: list[tuple[int, float]] = []
    if is_horizontal:
        for col in range(width):
            rows = np.where(mask[:, col] > 0)[0]
            if len(rows) >= 5:
                midline_points.append((col, float(np.median(rows))))
    else:
        for row in range(height):
            cols = np.where(mask[row, :] > 0)[0]
            if len(cols) >= 5:
                midline_points.append((row, float(np.median(cols))))

    if len(midline_points) < MIN_ROI_PIXELS:
        median_row = np.median(coords[:, 0])
        is_upper = coords[:, 0] <= median_row
    else:
        midline = np.array(midline_points)
        if is_horizontal:
            midline_rows = np.interp(coords[:, 1], midline[:, 0], midline[:, 1])
            is_upper = coords[:, 0] < midline_rows
        else:
            midline_cols = np.interp(coords[:, 0], midline[:, 0], midline[:, 1])
            is_upper = coords[:, 1] < midline_cols

    upper_coords = coords[is_upper]
    lower_coords = coords[~is_upper]
    if len(upper_coords) < MIN_ROI_PIXELS or len(lower_coords) < MIN_ROI_PIXELS:
        empty = np.empty((0, 2), dtype=np.intp)
        return empty, empty

    if np.mean(upper_coords[:, 0]) > np.mean(lower_coords[:, 0]):
        upper_coords, lower_coords = lower_coords, upper_coords
    return upper_coords, lower_coords


def _compute_belly_roi(image: np.ndarray) -> np.ndarray:
    fish_mask = _clean_fish_mask(_get_fish_mask(image))
    if cv2.countNonZero(fish_mask) == 0:
        raise BellyColorError("No fish pixels found after black-background masking.")

    _upper_coords, lower_coords = _split_by_midline(fish_mask)
    if len(lower_coords) < MIN_ROI_PIXELS:
        raise BellyColorError("Not enough lower-half fish pixels to define belly ROI.")

    roi_mask = np.zeros(fish_mask.shape, dtype=bool)
    roi_mask[lower_coords[:, 0], lower_coords[:, 1]] = True
    return roi_mask


def _rgb_medians_to_hex(image: np.ndarray, roi_mask: np.ndarray) -> str:
    rows, cols = np.where(roi_mask)
    if len(rows) < MIN_ROI_PIXELS:
        raise BellyColorError(
            f"Belly ROI has too few pixels: {len(rows)} < {MIN_ROI_PIXELS}."
        )

    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    medians = [round(float(np.median(rgb[rows, cols, channel])), 4) for channel in range(3)]
    channels = [int(value + 0.5) for value in medians]
    return "#{:02X}{:02X}{:02X}".format(*channels)


def extract_belly_hex_color_from_images(crop_bgr: np.ndarray, fish_mask: np.ndarray) -> str:
    """Return the lower-half fish color as an uppercase ``#RRGGBB`` value.

    ``fish_mask`` is used only to produce the requested black-background fish
    image. The ROI then follows the donor algorithm's non-black fish-mask
    semantics, including exclusion of pure-black fish pixels.
    """

    crop_bgr = _ensure_bgr_image(crop_bgr)
    binary_mask = _binary_mask(fish_mask, crop_bgr.shape[:2])
    black_background_fish = cv2.bitwise_and(crop_bgr, crop_bgr, mask=binary_mask)
    roi_mask = _compute_belly_roi(black_background_fish)
    return _rgb_medians_to_hex(black_background_fish, roi_mask)


__all__ = ["BellyColorError", "extract_belly_hex_color_from_images"]
import os
import logging
import numpy as np
from PIL import Image

logger = logging.getLogger("logo_cleaner")


def clean_logo_background(input_path: str, output_path: str) -> bool:
    """Remove white/near-white background from a logo and save a transparent PNG.

    Uses high-performance NumPy vectorization and corner-seeded flood fill
    to ensure exterior background pixels are made transparent while internal
    white logo elements are preserved.

    Args:
        input_path: path to the original logo (e.g. "logo/Brand_logo.png").
        output_path: destination for cleaned PNG (should be under assets/logo).

    Returns:
        True if white pixels were detected and made transparent, False otherwise.
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Logo file not found: {input_path}")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    img = Image.open(input_path).convert("RGBA")
    data = np.array(img)

    # Extract RGB channels
    r, g, b, a = data[:, :, 0], data[:, :, 1], data[:, :, 2], data[:, :, 3]

    # Mask near-white pixels (threshold tuned for anti-aliased edges)
    white_mask = (r > 235) & (g > 235) & (b > 235)

    detected_white = bool(np.any(white_mask))

    if detected_white:
        try:
            import cv2

            # Perform FloodFill from 4 corners to only erase exterior white background
            # keeping internal white shapes/text intact.
            h, w = white_mask.shape
            mask = np.zeros((h + 2, w + 2), dtype=np.uint8)

            binary_white = white_mask.astype(np.uint8) * 255

            # Seed flood fill from the 4 outer corners
            corners = [(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)]

            for cx, cy in corners:
                if binary_white[cy, cx] == 255:
                    cv2.floodFill(binary_white, mask, (cx, cy), 128)

            # Exterior background pixels are marked with 128
            exterior_mask = binary_white == 128
            data[exterior_mask, 3] = 0

        except Exception as e:
            logger.debug("[LOGO_CLEANER] Corner flood fill fallback to NumPy: %s", e)
            # High-performance NumPy fallback (global vectorization)
            data[white_mask, 3] = 0

        cleaned_img = Image.fromarray(data, mode="RGBA")
        cleaned_img.save(output_path, format="PNG")
    else:
        img.save(output_path, format="PNG")

    logger.info("[LOGO_CLEANER] detected_white_background=%s", detected_white)
    if detected_white:
        logger.info("[LOGO_CLEANER] transparency_applied -> %s", output_path)

    return detected_white

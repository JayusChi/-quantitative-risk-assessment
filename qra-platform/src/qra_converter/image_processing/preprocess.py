"""Safe image decode and traceable non-destructive preprocessing."""

from __future__ import annotations

import io
import os
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from ..parsing.geometry import AffineTransform
from .quality import ImageQuality, assess_image_quality

PREPROCESSING_VERSION = "qra.image-preprocess/1.0.0"


def _positive_limit(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except ValueError:
        return default
    return value if value > 0 else default


MAX_IMAGE_PIXELS = _positive_limit("QRA_PARSE_MAX_IMAGE_PIXELS", 50_000_000)
MAX_DECODED_IMAGE_BYTES = _positive_limit("QRA_PARSE_MAX_DECODED_IMAGE_BYTES", 200 * 1024 * 1024)


@dataclass(frozen=True)
class PreprocessedImage:
    image_bytes: bytes
    content_type: str
    original_width: int
    original_height: int
    width: int
    height: int
    exif_orientation: int
    original_to_processed: AffineTransform
    processing_steps: tuple[dict[str, object], ...]
    quality: ImageQuality


def _orientation_transform(orientation: int, width: int, height: int) -> AffineTransform:
    transforms = {
        1: AffineTransform(),
        2: AffineTransform(-1, 0, 0, 1, width, 0),
        3: AffineTransform(-1, 0, 0, -1, width, height),
        4: AffineTransform(1, 0, 0, -1, 0, height),
        5: AffineTransform(0, 1, 1, 0, 0, 0),
        6: AffineTransform(0, 1, -1, 0, height, 0),
        7: AffineTransform(0, -1, -1, 0, height, width),
        8: AffineTransform(0, -1, 1, 0, 0, width),
    }
    return transforms.get(orientation, AffineTransform())


def preprocess_image(
    content: bytes,
    *,
    grayscale: bool = True,
    autocontrast: bool = True,
) -> PreprocessedImage:
    try:
        with Image.open(io.BytesIO(content)) as opened:
            opened.verify()
        with Image.open(io.BytesIO(content)) as opened:
            original_width, original_height = opened.size
            if original_width <= 0 or original_height <= 0:
                raise ValueError("图像尺寸无效")
            pixels = original_width * original_height
            bands = max(1, len(opened.getbands()))
            if pixels > MAX_IMAGE_PIXELS or pixels * bands > MAX_DECODED_IMAGE_BYTES:
                raise ValueError("图像像素或解码内存超过解析上限")
            orientation = int(opened.getexif().get(274, 1) or 1)
            image = ImageOps.exif_transpose(opened).copy()
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise ValueError("图像损坏、格式无效或超过安全解码限制") from exc

    steps: list[dict[str, object]] = []
    transform = _orientation_transform(orientation, original_width, original_height)
    if orientation != 1:
        steps.append({"operation": "EXIF_TRANSPOSE", "orientation": orientation})
    if grayscale:
        image = image.convert("L")
        steps.append({"operation": "GRAYSCALE", "mode": "L"})
    if autocontrast:
        image = ImageOps.autocontrast(image, cutoff=1)
        steps.append({"operation": "AUTOCONTRAST", "cutoff_percent": 1})
    quality = assess_image_quality(image)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=False, compress_level=6)
    return PreprocessedImage(
        image_bytes=output.getvalue(),
        content_type="image/png",
        original_width=original_width,
        original_height=original_height,
        width=image.width,
        height=image.height,
        exif_orientation=orientation,
        original_to_processed=transform,
        processing_steps=tuple(steps),
        quality=quality,
    )


__all__ = [
    "MAX_DECODED_IMAGE_BYTES",
    "MAX_IMAGE_PIXELS",
    "PREPROCESSING_VERSION",
    "PreprocessedImage",
    "preprocess_image",
]

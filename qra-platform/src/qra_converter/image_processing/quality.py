"""Bounded, deterministic image quality measurements."""

from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageStat


@dataclass(frozen=True)
class ImageQuality:
    width: int
    height: int
    mean_luminance: float
    edge_variance: float
    overexposed_ratio: float
    underexposed_ratio: float
    estimated_skew_degrees: float
    border_dark_ratio: float
    blurry: bool
    overexposed: bool
    underexposed: bool
    low_resolution: bool
    skewed: bool
    clipped_content: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "width": self.width,
            "height": self.height,
            "mean_luminance": round(self.mean_luminance, 6),
            "edge_variance": round(self.edge_variance, 6),
            "overexposed_ratio": round(self.overexposed_ratio, 6),
            "underexposed_ratio": round(self.underexposed_ratio, 6),
            "estimated_skew_degrees": round(self.estimated_skew_degrees, 6),
            "border_dark_ratio": round(self.border_dark_ratio, 6),
            "blurry": self.blurry,
            "overexposed": self.overexposed,
            "underexposed": self.underexposed,
            "low_resolution": self.low_resolution,
            "skewed": self.skewed,
            "clipped_content": self.clipped_content,
        }


def assess_image_quality(image: Image.Image) -> ImageQuality:
    gray = image.convert("L")
    sample = gray.copy()
    sample.thumbnail((1024, 1024))
    histogram = sample.histogram()
    pixel_count = max(1, sample.width * sample.height)
    under = sum(histogram[:20]) / pixel_count
    over = sum(histogram[236:]) / pixel_count
    luminance = float(ImageStat.Stat(sample).mean[0])
    edges = sample.filter(ImageFilter.FIND_EDGES)
    variance = float(ImageStat.Stat(edges).var[0])
    pixels = sample.load()
    stride = max(1, int(math.sqrt(pixel_count / 100_000)))
    dark_points = [
        (x, y)
        for y in range(0, sample.height, stride)
        for x in range(0, sample.width, stride)
        if pixels[x, y] < 96
    ]
    skew_degrees = 0.0
    if len(dark_points) >= 20:
        mean_x = sum(point[0] for point in dark_points) / len(dark_points)
        mean_y = sum(point[1] for point in dark_points) / len(dark_points)
        covariance_xx = sum((x - mean_x) ** 2 for x, _ in dark_points)
        covariance_yy = sum((y - mean_y) ** 2 for _, y in dark_points)
        covariance_xy = sum((x - mean_x) * (y - mean_y) for x, y in dark_points)
        skew_degrees = math.degrees(
            0.5
            * math.atan2(
                2 * covariance_xy,
                covariance_xx - covariance_yy,
            )
        )
        if skew_degrees > 45:
            skew_degrees -= 90
        elif skew_degrees < -45:
            skew_degrees += 90
    border = max(1, min(sample.size) // 100)
    border_pixels = 0
    dark_border_pixels = 0
    for y in range(sample.height):
        for x in range(sample.width):
            if (
                x < border
                or y < border
                or x >= sample.width - border
                or y >= sample.height - border
            ):
                border_pixels += 1
                dark_border_pixels += pixels[x, y] < 96
    border_dark_ratio = dark_border_pixels / max(1, border_pixels)
    return ImageQuality(
        width=image.width,
        height=image.height,
        mean_luminance=luminance,
        edge_variance=variance,
        overexposed_ratio=over,
        underexposed_ratio=under,
        estimated_skew_degrees=skew_degrees,
        border_dark_ratio=border_dark_ratio,
        blurry=variance < 85.0,
        overexposed=(over >= 0.985 and variance < 250.0) or luminance >= 252.0,
        underexposed=(under >= 0.9 and variance < 250.0) or luminance <= 5.0,
        low_resolution=min(image.size) < 600,
        skewed=1.5 <= abs(skew_degrees) <= 20,
        clipped_content=border_dark_ratio >= 0.02,
    )


__all__ = ["ImageQuality", "assess_image_quality"]

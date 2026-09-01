"""Provider-neutral OCR media and serialized-request budgets.

The upload limit is deliberately not represented here: it protects the intake
boundary, while this policy protects each outbound model request after local
page rendering, compression and tiling.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name}必须是整数") from exc
    if not minimum <= value <= maximum:
        raise ValueError(f"{name}必须在{minimum}到{maximum}之间")
    return value


@dataclass(frozen=True, slots=True)
class OcrPayloadPolicy:
    policy_id: str = "aliyun-bailian-conservative"
    version: str = "qra.ocr-payload-policy/1.0.0"
    max_data_uri_bytes: int = 9_000_000
    max_http_body_bytes: int = 9_500_000
    preferred_raw_image_bytes: int = 7 * 1024 * 1024
    model_min_pixels: int = 3_072
    model_max_pixels: int = 8_388_608
    minimum_side_pixels: int = 10
    maximum_aspect_ratio: float = 200.0
    allowed_formats: tuple[str, ...] = ("PNG", "JPEG")
    jpeg_quality_ladder: tuple[int, ...] = (90, 82, 72)
    tile_overlap_pixels: int = 96
    tile_overlap_ratio: float = 0.03
    maximum_tiles_per_page: int = 32
    minimum_tile_pixels: int = 256

    def __post_init__(self) -> None:
        if self.max_data_uri_bytes < 1_024 or self.max_http_body_bytes < 1_024:
            raise ValueError("OCR外发预算过小")
        if self.max_http_body_bytes <= self.max_data_uri_bytes:
            raise ValueError("HTTP请求预算必须大于data URI预算")
        if not 100 <= self.model_min_pixels <= self.model_max_pixels:
            raise ValueError("OCR模型像素范围无效")
        if not 10 <= self.minimum_side_pixels <= 4_096:
            raise ValueError("OCR最小边长无效")
        if not 1.0 <= self.maximum_aspect_ratio <= 1_000.0:
            raise ValueError("OCR宽高比上限无效")
        if not 1 <= self.maximum_tiles_per_page <= 256:
            raise ValueError("OCR单页切片数上限无效")
        if not 0 <= self.tile_overlap_pixels <= 4_096:
            raise ValueError("OCR切片重叠像素无效")
        if not 0 <= self.tile_overlap_ratio <= 0.5:
            raise ValueError("OCR切片重叠比例无效")
        if not self.allowed_formats or any(
            item not in {"PNG", "JPEG"} for item in self.allowed_formats
        ):
            raise ValueError("OCR图片编码白名单无效")
        if any(not 30 <= quality <= 95 for quality in self.jpeg_quality_ladder):
            raise ValueError("OCR JPEG质量档位无效")

    @classmethod
    def from_environment(cls) -> OcrPayloadPolicy:
        return cls(
            max_data_uri_bytes=_bounded_int(
                "QRA_OCR_MAX_DATA_URI_BYTES", 9_000_000, 64 * 1024, 10 * 1024 * 1024
            ),
            max_http_body_bytes=_bounded_int(
                "QRA_OCR_MAX_REQUEST_BYTES", 9_500_000, 128 * 1024, 32 * 1024 * 1024
            ),
            model_max_pixels=_bounded_int(
                "QRA_OCR_TARGET_MAX_PIXELS", 8_388_608, 100_000, 50_000_000
            ),
            maximum_tiles_per_page=_bounded_int(
                "QRA_OCR_MAX_TILES_PER_PAGE", 32, 1, 256
            ),
            tile_overlap_pixels=_bounded_int(
                "QRA_OCR_TILE_OVERLAP_PIXELS", 96, 0, 4_096
            ),
        )

    def to_metadata(self) -> dict[str, object]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "max_data_uri_bytes": self.max_data_uri_bytes,
            "max_http_body_bytes": self.max_http_body_bytes,
            "preferred_raw_image_bytes": self.preferred_raw_image_bytes,
            "model_min_pixels": self.model_min_pixels,
            "model_max_pixels": self.model_max_pixels,
            "minimum_side_pixels": self.minimum_side_pixels,
            "maximum_aspect_ratio": self.maximum_aspect_ratio,
            "allowed_formats": list(self.allowed_formats),
            "jpeg_quality_ladder": list(self.jpeg_quality_ladder),
            "tile_overlap_pixels": self.tile_overlap_pixels,
            "tile_overlap_ratio": self.tile_overlap_ratio,
            "maximum_tiles_per_page": self.maximum_tiles_per_page,
            "minimum_tile_pixels": self.minimum_tile_pixels,
        }


__all__ = ["OcrPayloadPolicy"]

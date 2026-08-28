"""Coordinate conversion and validation for source evidence geometry."""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import BoundingBox


def pdf_bottom_left_to_top_left(
    *, x0: float, y0: float, x1: float, y1: float, page_height: float
) -> BoundingBox:
    """Convert a PDF bottom-left box into the parsing contract coordinate space."""
    return BoundingBox(float(x0), float(page_height - y1), float(x1 - x0), float(y1 - y0))


def validate_bbox(
    bbox: BoundingBox | None,
    *,
    width: float | None,
    height: float | None,
    tolerance: float = 0.01,
) -> bool:
    if bbox is None:
        return True
    values = (bbox.x, bbox.y, bbox.width, bbox.height)
    if any(value != value or value in (float("inf"), float("-inf")) for value in values):
        return False
    if bbox.x < -tolerance or bbox.y < -tolerance or bbox.width < 0 or bbox.height < 0:
        return False
    if width is not None and bbox.right > width + tolerance:
        return False
    if height is not None and bbox.bottom > height + tolerance:
        return False
    return True


@dataclass(frozen=True)
class AffineTransform:
    """Six-value affine matrix mapping ``(x, y)`` to ``(x', y')``."""

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    e: float = 0.0
    f: float = 0.0

    def point(self, x: float, y: float) -> tuple[float, float]:
        return self.a * x + self.c * y + self.e, self.b * x + self.d * y + self.f

    def box(self, bbox: BoundingBox) -> BoundingBox:
        points = (
            self.point(bbox.x, bbox.y),
            self.point(bbox.right, bbox.y),
            self.point(bbox.x, bbox.bottom),
            self.point(bbox.right, bbox.bottom),
        )
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        return BoundingBox(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def inverse(self) -> AffineTransform:
        determinant = self.a * self.d - self.b * self.c
        if abs(determinant) < 1e-12:
            raise ValueError("坐标变换矩阵不可逆")
        return AffineTransform(
            self.d / determinant,
            -self.b / determinant,
            -self.c / determinant,
            self.a / determinant,
            (self.c * self.f - self.d * self.e) / determinant,
            (self.b * self.e - self.a * self.f) / determinant,
        )

    def values(self) -> tuple[float, float, float, float, float, float]:
        return (self.a, self.b, self.c, self.d, self.e, self.f)


__all__ = ["AffineTransform", "pdf_bottom_left_to_top_left", "validate_bbox"]

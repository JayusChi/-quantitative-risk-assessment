"""Deterministic OCR image encoding, scaling, tiling and coordinate merge."""

from __future__ import annotations

import hashlib
import io
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from PIL import Image

from ..ocr.payload_policy import OcrPayloadPolicy
from ..ocr.ports import OcrResponse, OcrTable, OcrTextBlock
from ..parsing.contracts import BoundingBox
from ..parsing.geometry import AffineTransform
from .preprocess import PreprocessedImage

IMAGE_DERIVATION_VERSION = "qra.ocr-image-derivation/1.0.0"


@dataclass(frozen=True, slots=True)
class OcrImageUnit:
    unit_id: str
    page_number: int
    region_kind: str
    tile_index: int
    tile_count: int
    encoded_bytes: bytes
    content_type: str
    sha256: str
    width: int
    height: int
    encoded_byte_count: int
    bbox_in_processed_image: BoundingBox
    tile_to_processed_transform: AffineTransform
    processed_to_original_transform: AffineTransform
    processing_steps: tuple[dict[str, object], ...]
    payload_policy_version: str


@dataclass(frozen=True, slots=True)
class OcrImagePlan:
    units: tuple[OcrImageUnit, ...]
    issues: tuple[str, ...]
    processed_width: int
    processed_height: int
    scale: float


@dataclass(frozen=True, slots=True)
class MergedOcrResult:
    text_blocks: tuple[OcrTextBlock, ...]
    tables: tuple[OcrTable, ...]
    issues: tuple[str, ...]
    status: str
    successful_unit_count: int
    failed_unit_count: int


SerializedSize = Callable[[bytes, str, int, int, str], int]


def _encoded_candidates(image: Image.Image, policy: OcrPayloadPolicy):
    if "PNG" in policy.allowed_formats:
        output = io.BytesIO()
        image.convert("L").save(output, format="PNG", optimize=True, compress_level=9)
        yield output.getvalue(), "image/png", "PNG", None
    if "JPEG" in policy.allowed_formats:
        for quality in policy.jpeg_quality_ladder:
            output = io.BytesIO()
            image.convert("L").save(
                output,
                format="JPEG",
                quality=quality,
                optimize=True,
                progressive=False,
            )
            yield output.getvalue(), "image/jpeg", "JPEG", quality


def _data_uri_size(content: bytes, content_type: str) -> int:
    return len(f"data:{content_type};base64,".encode("ascii")) + 4 * math.ceil(
        len(content) / 3
    )


def _stable_unit_id(
    *, page_number: int, region_kind: str, bbox: tuple[int, int, int, int]
) -> str:
    value = f"{page_number}\0{region_kind}\0{bbox[0]}\0{bbox[1]}\0{bbox[2]}\0{bbox[3]}"
    return "OCRU-" + hashlib.sha256(value.encode()).hexdigest()[:20]


def _initial_boxes(width: int, height: int, overlap: int) -> list[tuple[int, int, int, int]]:
    # Strip images are tiled before lossy downscaling so small glyphs remain readable.
    ratio = max(width / max(1, height), height / max(1, width))
    if ratio <= 8.0:
        return [(0, 0, width, height)]
    horizontal = width >= height
    long_side = width if horizontal else height
    short_side = height if horizontal else width
    span = max(short_side * 8, short_side + 1)
    step = max(1, span - overlap)
    boxes = []
    start = 0
    while start < long_side:
        end = min(long_side, start + span)
        boxes.append((start, 0, end, height) if horizontal else (0, start, width, end))
        if end == long_side:
            break
        start += step
    return boxes


def _split_box(
    box: tuple[int, int, int, int], overlap: int
) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]] | None:
    left, top, right, bottom = box
    width, height = right - left, bottom - top
    if width >= height and width >= 2:
        middle = left + width // 2
        pad = min(overlap, max(0, width // 4))
        return (left, top, min(right, middle + pad), bottom), (
            max(left, middle - pad),
            top,
            right,
            bottom,
        )
    if height >= 2:
        middle = top + height // 2
        pad = min(overlap, max(0, height // 4))
        return (left, top, right, min(bottom, middle + pad)), (
            left,
            max(top, middle - pad),
            right,
            bottom,
        )
    return None


def plan_ocr_image_units(
    processed: PreprocessedImage,
    *,
    policy: OcrPayloadPolicy,
    page_number: int,
    region_kind: str = "PAGE",
    serialized_size: SerializedSize | None = None,
) -> OcrImagePlan:
    """Return only units that pass actual media and final-body byte budgets."""

    with Image.open(io.BytesIO(processed.image_bytes)) as opened:
        image = opened.convert("L").copy()
    original_processed_width, original_processed_height = image.size
    scale = min(
        1.0,
        math.sqrt(policy.model_max_pixels / max(1, image.width * image.height)),
    )
    if scale < 1.0:
        width = max(policy.minimum_side_pixels, round(image.width * scale))
        height = max(policy.minimum_side_pixels, round(image.height * scale))
        image = image.resize((width, height), Image.Resampling.LANCZOS)
        scale = width / original_processed_width
    else:
        scale = 1.0

    overlap = max(
        policy.tile_overlap_pixels,
        round(min(image.size) * policy.tile_overlap_ratio),
    )
    queue = _initial_boxes(image.width, image.height, overlap)
    planned: list[OcrImageUnit] = []
    issues: list[str] = []
    while queue:
        if len(planned) + len(queue) > policy.maximum_tiles_per_page:
            issues.append("PARSE.OCR_TILE_LIMIT_EXCEEDED")
            break
        box = queue.pop(0)
        left, top, right, bottom = box
        crop = image.crop(box)
        unit_id = _stable_unit_id(page_number=page_number, region_kind=region_kind, bbox=box)
        selected: tuple[bytes, str, str, int | None, int] | None = None
        for content, content_type, encoding, quality in _encoded_candidates(crop, policy):
            data_uri_count = _data_uri_size(content, content_type)
            body_count = (
                serialized_size(content, content_type, crop.width, crop.height, unit_id)
                if serialized_size is not None
                else data_uri_count + 4_096
            )
            if (
                data_uri_count <= policy.max_data_uri_bytes
                and body_count <= policy.max_http_body_bytes
            ):
                selected = content, content_type, encoding, quality, body_count
                break
        if selected is None:
            if max(crop.size) <= policy.minimum_tile_pixels:
                issues.append("PARSE.OCR_REQUEST_TOO_LARGE")
                continue
            children = _split_box(box, overlap)
            if children is None or children[0] == box or children[1] == box:
                issues.append("PARSE.OCR_REQUEST_TOO_LARGE")
                continue
            queue[0:0] = list(children)
            continue
        content, content_type, encoding, quality, body_count = selected
        inverse_scale = 1.0 / scale
        bbox_processed = BoundingBox(
            left * inverse_scale,
            top * inverse_scale,
            (right - left) * inverse_scale,
            (bottom - top) * inverse_scale,
        )
        steps = [*processed.processing_steps]
        if scale < 1.0:
            steps.append(
                {
                    "operation": "RESIZE_MAX_PIXELS",
                    "scale": scale,
                    "width": image.width,
                    "height": image.height,
                }
            )
        if box != (0, 0, image.width, image.height):
            steps.append(
                {
                    "operation": "TILE",
                    "bbox": [left, top, right - left, bottom - top],
                    "overlap_pixels": overlap,
                }
            )
        steps.append(
            {
                "operation": "ENCODE",
                "format": encoding,
                "quality": quality,
                "encoded_byte_count": len(content),
                "request_byte_count": body_count,
            }
        )
        planned.append(
            OcrImageUnit(
                unit_id=unit_id,
                page_number=page_number,
                region_kind=region_kind,
                tile_index=0,
                tile_count=0,
                encoded_bytes=content,
                content_type=content_type,
                sha256=hashlib.sha256(content).hexdigest(),
                width=crop.width,
                height=crop.height,
                encoded_byte_count=len(content),
                bbox_in_processed_image=bbox_processed,
                tile_to_processed_transform=AffineTransform(
                    inverse_scale, 0, 0, inverse_scale, left * inverse_scale, top * inverse_scale
                ),
                processed_to_original_transform=processed.original_to_processed.inverse(),
                processing_steps=tuple(steps),
                payload_policy_version=policy.version,
            )
        )
    total = len(planned)
    units = tuple(
        replace(unit, tile_index=index, tile_count=total)
        for index, unit in enumerate(planned, start=1)
    )
    return OcrImagePlan(
        units=units,
        issues=tuple(dict.fromkeys(issues)),
        processed_width=original_processed_width,
        processed_height=original_processed_height,
        scale=scale,
    )


def _overlap_ratio(first: BoundingBox, second: BoundingBox) -> float:
    x = max(0.0, min(first.right, second.right) - max(first.x, second.x))
    y = max(0.0, min(first.bottom, second.bottom) - max(first.y, second.y))
    intersection = x * y
    return intersection / max(1e-9, min(first.width * first.height, second.width * second.height))


def merge_ocr_unit_responses(
    results: Sequence[tuple[OcrImageUnit, OcrResponse | None]],
) -> MergedOcrResult:
    blocks: list[OcrTextBlock] = []
    tables: list[OcrTable] = []
    issues: list[str] = []
    success = 0
    failed = 0
    for unit, response in results:
        if response is None:
            failed += 1
            issues.append("PARSE.OCR_TILE_FAILED")
            continue
        success += 1
        to_original = unit.processed_to_original_transform
        for block in response.text_blocks:
            mapped = to_original.box(unit.tile_to_processed_transform.box(block.bbox))
            candidate = replace(block, bbox=mapped)
            duplicate = next(
                (
                    index
                    for index, existing in enumerate(blocks)
                    if " ".join(existing.text.split()).casefold()
                    == " ".join(candidate.text.split()).casefold()
                    and _overlap_ratio(existing.bbox, candidate.bbox) >= 0.5
                ),
                None,
            )
            if duplicate is not None:
                if candidate.confidence > blocks[duplicate].confidence:
                    blocks[duplicate] = candidate
                continue
            if any(
                _overlap_ratio(existing.bbox, candidate.bbox) >= 0.7
                and " ".join(existing.text.split()).casefold()
                != " ".join(candidate.text.split()).casefold()
                for existing in blocks
            ):
                issues.append("PARSE.OCR_OVERLAP_CONFLICT")
            blocks.append(candidate)
        for table in response.tables:
            table_bbox = (
                to_original.box(unit.tile_to_processed_transform.box(table.bbox))
                if table.bbox
                else None
            )
            cells = tuple(
                replace(
                    cell,
                    bbox=(
                        to_original.box(unit.tile_to_processed_transform.box(cell.bbox))
                        if cell.bbox
                        else None
                    ),
                )
                for cell in table.cells
            )
            tables.append(replace(table, bbox=table_bbox, cells=cells))
        if response.finish_reason == "length":
            issues.append("PARSE.OCR_OUTPUT_TRUNCATED")
    blocks.sort(key=lambda block: (block.bbox.y, block.bbox.x, block.text))
    if success and failed:
        issues.append("PARSE.OCR_PARTIAL")
        status = "PARTIAL"
    elif success:
        status = "PARTIAL" if "PARSE.OCR_OUTPUT_TRUNCATED" in issues else "COMPLETE"
    else:
        status = "FAILED"
    return MergedOcrResult(
        text_blocks=tuple(blocks),
        tables=tuple(tables),
        issues=tuple(dict.fromkeys(issues)),
        status=status,
        successful_unit_count=success,
        failed_unit_count=failed,
    )


def split_ocr_image_unit(
    unit: OcrImageUnit,
    *,
    policy: OcrPayloadPolicy,
    serialized_size: SerializedSize | None = None,
) -> tuple[OcrImageUnit, ...]:
    """Bisect one rejected/truncated unit while preserving its coordinate chain."""

    with Image.open(io.BytesIO(unit.encoded_bytes)) as opened:
        image = opened.convert("L").copy()
    overlap = min(
        policy.tile_overlap_pixels,
        max(0, max(image.size) // 4),
    )
    boxes = _split_box((0, 0, image.width, image.height), overlap)
    if boxes is None:
        return ()
    children: list[OcrImageUnit] = []
    for child_index, box in enumerate(boxes, start=1):
        crop = image.crop(box)
        child_id = f"{unit.unit_id}-S{child_index}"
        selected = None
        for content, content_type, encoding, quality in _encoded_candidates(crop, policy):
            data_uri_count = _data_uri_size(content, content_type)
            body_count = (
                serialized_size(content, content_type, crop.width, crop.height, child_id)
                if serialized_size
                else data_uri_count + 4_096
            )
            if (
                data_uri_count <= policy.max_data_uri_bytes
                and body_count <= policy.max_http_body_bytes
            ):
                selected = content, content_type, encoding, quality, body_count
                break
        if selected is None:
            continue
        content, content_type, encoding, quality, body_count = selected
        left, top, right, bottom = box
        child_to_parent = AffineTransform(e=float(left), f=float(top))
        child_to_processed = unit.tile_to_processed_transform.compose(child_to_parent)
        children.append(
            OcrImageUnit(
                unit_id=child_id,
                page_number=unit.page_number,
                region_kind=unit.region_kind,
                tile_index=child_index,
                tile_count=2,
                encoded_bytes=content,
                content_type=content_type,
                sha256=hashlib.sha256(content).hexdigest(),
                width=crop.width,
                height=crop.height,
                encoded_byte_count=len(content),
                bbox_in_processed_image=child_to_processed.box(
                    BoundingBox(0, 0, crop.width, crop.height)
                ),
                tile_to_processed_transform=child_to_processed,
                processed_to_original_transform=unit.processed_to_original_transform,
                processing_steps=(
                    *unit.processing_steps,
                    {
                        "operation": "ADAPTIVE_BISECT",
                        "parent_unit_id": unit.unit_id,
                        "bbox": [left, top, right - left, bottom - top],
                    },
                    {
                        "operation": "ENCODE",
                        "format": encoding,
                        "quality": quality,
                        "encoded_byte_count": len(content),
                        "request_byte_count": body_count,
                    },
                ),
                payload_policy_version=policy.version,
            )
        )
    return tuple(children)


__all__ = [
    "IMAGE_DERIVATION_VERSION",
    "MergedOcrResult",
    "OcrImagePlan",
    "OcrImageUnit",
    "merge_ocr_unit_responses",
    "plan_ocr_image_units",
    "split_ocr_image_unit",
]

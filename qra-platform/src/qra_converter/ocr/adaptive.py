"""Bounded OCR adaptation shared by image files and rendered PDF page images."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from ..image_processing.ocr_planner import (
    MergedOcrResult,
    OcrImagePlan,
    OcrImageUnit,
    merge_ocr_unit_responses,
    plan_ocr_image_units,
    split_ocr_image_unit,
)
from ..image_processing.preprocess import PreprocessedImage
from .payload_policy import OcrPayloadPolicy
from .ports import OcrProviderError, OcrRequest, OcrRequestTooLarge, OcrResponse
from .service import OcrService


@dataclass(frozen=True, slots=True)
class AdaptiveOcrResult:
    merged: MergedOcrResult
    plan: OcrImagePlan
    attempted_units: tuple[OcrImageUnit, ...]
    adapted: bool
    responses: tuple[OcrResponse, ...]


def recognize_preprocessed_image(
    processed: PreprocessedImage,
    *,
    service: OcrService,
    source_id: str,
    page_number: int,
    request_prefix: str,
    languages: tuple[str, ...],
    timeout_seconds: float,
    region_kind: str = "PAGE",
    task_type: str = "advanced_recognition",
    detect_tables: bool = False,
    max_split_depth: int = 4,
    progress: Callable[[int, int], None] | None = None,
) -> AdaptiveOcrResult:
    policy = getattr(service.provider, "payload_policy", None)
    if not isinstance(policy, OcrPayloadPolicy):
        policy = OcrPayloadPolicy.from_environment()

    def request_for(
        content: bytes,
        content_type: str,
        width: int,
        height: int,
        unit_id: str,
        *,
        legacy_id: bool = False,
        parent_unit_id: str | None = None,
    ) -> OcrRequest:
        return OcrRequest(
            image_bytes=content,
            width=width,
            height=height,
            languages=languages,
            detect_tables=detect_tables,
            request_id=(request_prefix if legacy_id else f"{request_prefix}:{unit_id}:{task_type}"),
            timeout_seconds=timeout_seconds,
            image_content_type=content_type,
            source_id=source_id,
            page_number=page_number,
            region_id=f"{region_kind.casefold()}-{page_number}",
            tile_id=unit_id,
            region_kind=region_kind,
            task_type=task_type,
            payload_policy_version=policy.version,
            parent_call_id=(
                f"{request_prefix}:{parent_unit_id}:{task_type}"
                if parent_unit_id
                else None
            ),
        )

    def size(
        content: bytes, content_type: str, width: int, height: int, unit_id: str
    ) -> int:
        request = request_for(content, content_type, width, height, unit_id)
        return service.request_byte_count(request)

    plan = plan_ocr_image_units(
        processed,
        policy=policy,
        page_number=page_number,
        region_kind=region_kind,
        serialized_size=size,
    )
    queue: list[tuple[OcrImageUnit, int]] = [(unit, 0) for unit in plan.units]
    attempts: list[OcrImageUnit] = []
    results: list[tuple[OcrImageUnit, OcrResponse | None]] = []
    adapted = bool(plan.issues) or len(plan.units) > 1
    error_codes: list[str] = []
    completed = 0
    while queue:
        unit, depth = queue.pop(0)
        attempts.append(unit)
        request = request_for(
            unit.encoded_bytes,
            unit.content_type,
            unit.width,
            unit.height,
            unit.unit_id,
            legacy_id=(len(plan.units) == 1 and depth == 0 and task_type == "advanced_recognition"),
            parent_unit_id=next(
                (
                    str(step["parent_unit_id"])
                    for step in reversed(unit.processing_steps)
                    if step.get("operation") == "ADAPTIVE_BISECT"
                    and step.get("parent_unit_id")
                ),
                None,
            ),
        )
        try:
            response, _ = service.recognize(request)
        except OcrRequestTooLarge:
            error_codes.append("PARSE.OCR_REQUEST_TOO_LARGE")
            children = (
                split_ocr_image_unit(unit, policy=policy, serialized_size=size)
                if depth < max_split_depth
                else ()
            )
            within_limit = (
                len(attempts) + len(queue) + len(children)
                <= policy.maximum_tiles_per_page
            )
            if children and within_limit:
                queue[0:0] = [(child, depth + 1) for child in children]
                adapted = True
            else:
                results.append((unit, None))
        except OcrProviderError as exc:
            error_codes.append(exc.code)
            results.append((unit, None))
        else:
            if response.finish_reason == "length" and depth < max_split_depth:
                # Preserve partial text; retry smaller regions and merge overlap duplicates.
                results.append((unit, response))
                children = split_ocr_image_unit(unit, policy=policy, serialized_size=size)
                within_limit = (
                    len(attempts) + len(queue) + len(children)
                    <= policy.maximum_tiles_per_page
                )
                if children and within_limit:
                    queue[0:0] = [(child, depth + 1) for child in children]
                    adapted = True
            else:
                results.append((unit, response))
        completed += 1
        if progress is not None:
            progress(completed, completed + len(queue))
    merged = merge_ocr_unit_responses(results)
    merged = replace(
        merged,
        issues=tuple(dict.fromkeys((*plan.issues, *merged.issues, *error_codes))),
    )
    return AdaptiveOcrResult(
        merged,
        plan,
        tuple(attempts),
        adapted,
        tuple(response for _, response in results if response is not None),
    )


__all__ = ["AdaptiveOcrResult", "recognize_preprocessed_image"]

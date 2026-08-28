"""Conservative coordinate-only table inference used when OCR returns no grid."""

from __future__ import annotations

from collections.abc import Sequence
from statistics import median

from .contracts import ParsedCell, ParsedTable, TextBlock, source_fragment_sha256

INFERRED_TABLE_CONFIDENCE_CAP = 0.6
LAYOUT_RULE_VERSION = "coordinate-grid-v1"


def infer_table_from_blocks(
    blocks: Sequence[TextBlock],
    *,
    table_id: str,
    page_number: int | None,
    source_prefix: str,
) -> ParsedTable | None:
    positioned = [block for block in blocks if block.bbox is not None and block.text.strip()]
    if len(positioned) < 4:
        return None
    typical_height = median(block.bbox.height for block in positioned if block.bbox)
    row_tolerance = max(3.0, typical_height * 0.6)
    rows: list[list[TextBlock]] = []
    for block in sorted(positioned, key=lambda item: (item.bbox.y, item.bbox.x)):
        if not rows or abs(rows[-1][0].bbox.y - block.bbox.y) > row_tolerance:
            rows.append([block])
        else:
            rows[-1].append(block)
    rows = [sorted(row, key=lambda item: item.bbox.x) for row in rows if len(row) >= 2]
    if len(rows) < 2:
        return None
    column_count = len(rows[0])
    if column_count < 2 or any(len(row) != column_count for row in rows):
        return None
    reference_centers = [block.bbox.x + block.bbox.width / 2 for block in rows[0]]
    spread = max(reference_centers) - min(reference_centers)
    column_tolerance = max(5.0, spread / max(1, column_count - 1) * 0.35)
    for row in rows[1:]:
        centers = [block.bbox.x + block.bbox.width / 2 for block in row]
        if any(
            abs(current - reference) > column_tolerance
            for current, reference in zip(centers, reference_centers, strict=False)
        ):
            return None
    confidence = min(
        INFERRED_TABLE_CONFIDENCE_CAP,
        min(
            (
                float(block.confidence)
                for row in rows
                for block in row
                if block.confidence is not None
            ),
            default=INFERRED_TABLE_CONFIDENCE_CAP,
        ),
    )
    cells = tuple(
        ParsedCell(
            row_index=row_index,
            column_index=column_index,
            address=f"R{row_index + 1}C{column_index + 1}",
            raw_value=block.text,
            display_text=block.text,
            value_type="STRING",
            source_location=(
                f"{source_prefix};inferred-table:{table_id};"
                f"row:{row_index + 1};column:{column_index + 1}"
            ),
            extraction_method="INFERRED_TABLE_COORDINATE_CLUSTER",
            confidence=confidence,
            bbox=block.bbox,
            coordinate_space=block.coordinate_space,
            source_fragment_sha256=source_fragment_sha256(
                [LAYOUT_RULE_VERSION, block.block_id, block.text]
            ),
        )
        for row_index, row in enumerate(rows)
        for column_index, block in enumerate(row)
    )
    x0 = min(cell.bbox.x for cell in cells if cell.bbox)
    y0 = min(cell.bbox.y for cell in cells if cell.bbox)
    x1 = max(cell.bbox.right for cell in cells if cell.bbox)
    y1 = max(cell.bbox.bottom for cell in cells if cell.bbox)
    from .contracts import BoundingBox

    return ParsedTable(
        table_id=table_id,
        row_count=len(rows),
        column_count=column_count,
        cells=cells,
        extraction_method="INFERRED_TABLE_COORDINATE_CLUSTER",
        confidence=confidence,
        page_number=page_number,
        bbox=BoundingBox(x0, y0, x1 - x0, y1 - y0),
        coordinate_space=cells[0].coordinate_space,
        rule_version=LAYOUT_RULE_VERSION,
        metadata={"inferred": True, "confidence_cap": INFERRED_TABLE_CONFIDENCE_CAP},
    )


__all__ = [
    "INFERRED_TABLE_CONFIDENCE_CAP",
    "LAYOUT_RULE_VERSION",
    "infer_table_from_blocks",
]

"""OOXML-native DOCX parser with structural locations and image OCR isolation."""

from __future__ import annotations

import hashlib
import posixpath
import re
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from ..contracts import IssueSeverity
from ..image_processing.preprocess import preprocess_image
from ..ocr.ports import OcrProviderError, OcrRequest
from ..parsing.compatibility import legacy_read
from ..parsing.contracts import (
    CoordinateSpace,
    ImageRegion,
    ParsedCell,
    ParsedDocument,
    ParsedTable,
    ParseIssue,
    PreviewResource,
    ReaderOutput,
    TextBlock,
    source_fragment_sha256,
)
from ..parsing.layout import infer_table_from_blocks
from ..parsing.registry import ParseContext

WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
REL_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL_NAMESPACE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DRAWING_NAMESPACE = "http://schemas.openxmlformats.org/drawingml/2006/main"
W = f"{{{WORD_NAMESPACE}}}"
R = f"{{{OFFICE_REL_NAMESPACE}}}"
A = f"{{{DRAWING_NAMESPACE}}}"
PR = f"{{{REL_NAMESPACE}}}"


def _paragraph_text(element: ElementTree.Element) -> str:
    values: list[str] = []
    for node in element.iter():
        if node.tag == f"{W}t":
            values.append(node.text or "")
        elif node.tag == f"{W}tab":
            values.append("\t")
        elif node.tag in {f"{W}br", f"{W}cr"}:
            values.append("\n")
    return "".join(values)


def _cell_text(element: ElementTree.Element) -> str:
    return "\n".join(
        text
        for text in (_paragraph_text(paragraph) for paragraph in element.findall(f"./{W}p"))
        if text
    )


def _relationships(package: ZipFile, part: str) -> dict[str, str]:
    part_path = PurePosixPath(part)
    rels_path = part_path.parent / "_rels" / f"{part_path.name}.rels"
    try:
        root = ElementTree.fromstring(package.read(str(rels_path)))
    except (KeyError, ElementTree.ParseError):
        return {}
    result: dict[str, str] = {}
    for rel in root.findall(f"./{PR}Relationship"):
        relationship_id = rel.attrib.get("Id")
        target = rel.attrib.get("Target")
        if not relationship_id or not target or rel.attrib.get("TargetMode") == "External":
            continue
        resolved = posixpath.normpath(posixpath.join(str(part_path.parent), target))
        if resolved.startswith("../") or resolved.startswith("/"):
            continue
        result[relationship_id] = resolved
    return result


class DocxReader:
    reader_id = "docx/ooxml"
    parser_version = "2.0.0"
    media_types = frozenset(
        {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    )

    def parse(self, path: Path, context: ParseContext) -> ReaderOutput:
        if context.page_progress:
            context.page_progress(1, 1, "正在解析DOCX结构")
        issues: list[ParseIssue] = []
        blocks: list[TextBlock] = []
        tables: list[ParsedTable] = []
        images: list[ImageRegion] = []
        resources: list[PreviewResource] = []
        ocr_calls: list[dict[str, object]] = []
        try:
            package = ZipFile(path)
        except BadZipFile as exc:
            raise ValueError(f"DOCX结构无效：{path.name}") from exc
        try:
            names = set(package.namelist())
            try:
                document_root = ElementTree.fromstring(package.read("word/document.xml"))
            except (KeyError, ElementTree.ParseError) as exc:
                raise ValueError(f"DOCX正文XML无效：{path.name}") from exc
            if "word/comments.xml" in names or document_root.findall(f".//{W}commentRangeStart"):
                issues.append(
                    ParseIssue(
                        "PARSE.UNSUPPORTED_EMBEDDED_OBJECT",
                        "DOCX包含批注，已记录存在性但未合并到正文",
                        IssueSeverity.WARNING,
                        location="word/comments.xml",
                    )
                )
            if document_root.findall(f".//{W}ins") or document_root.findall(f".//{W}del"):
                issues.append(
                    ParseIssue(
                        "PARSE.UNSUPPORTED_EMBEDDED_OBJECT",
                        "DOCX包含修订标记，正文按当前OOXML可见文本提取",
                        IssueSeverity.WARNING,
                        location="word/document.xml",
                    )
                )
            if document_root.findall(f".//{W}txbxContent"):
                issues.append(
                    ParseIssue(
                        "PARSE.UNSUPPORTED_EMBEDDED_OBJECT",
                        "DOCX包含文本框，已记录但不把其内容伪装成相邻段落",
                        IssueSeverity.WARNING,
                        location="word/document.xml",
                    )
                )
            embedded = [name for name in names if name.startswith("word/embeddings/")]
            if embedded:
                issues.append(
                    ParseIssue(
                        "PARSE.UNSUPPORTED_EMBEDDED_OBJECT",
                        f"DOCX包含{len(embedded)}个未执行的嵌入对象",
                        IssueSeverity.WARNING,
                        location="word/embeddings",
                    )
                )

            body = document_root.find(f"./{W}body")
            paragraph_index = 0
            table_index = 0
            if body is not None:
                for child in list(body):
                    if context.cancel_check:
                        context.cancel_check()
                    if child.tag == f"{W}p":
                        paragraph_index += 1
                        text = _paragraph_text(child)
                        if text:
                            style = child.find(f"./{W}pPr/{W}pStyle")
                            style_name = style.attrib.get(f"{W}val") if style is not None else None
                            heading = None
                            if style_name:
                                match = re.search(r"(?:Heading|标题)\s*([1-9])", style_name, re.I)
                                if match:
                                    heading = int(match.group(1))
                            bold_nodes = child.findall(f".//{W}rPr/{W}b")
                            blocks.append(
                                TextBlock(
                                    block_id=f"paragraph-{paragraph_index}",
                                    text=text,
                                    normalized_text=" ".join(text.split()),
                                    reading_order=len(blocks) + 1,
                                    block_type="HEADING" if heading else "PARAGRAPH",
                                    extraction_method="DOCX_NATIVE_TEXT",
                                    source_fragment_sha256=source_fragment_sha256(text),
                                    structure_location=(
                                        f"word/document.xml;paragraph:{paragraph_index}"
                                    ),
                                    coordinate_space=CoordinateSpace.DOCX_STRUCTURE,
                                    confidence=1.0,
                                    style_hint={
                                        "style_name": style_name,
                                        "heading_level": heading,
                                        "bold": bool(bold_nodes),
                                    },
                                )
                            )
                    elif child.tag == f"{W}tbl":
                        table_index += 1
                        tables.append(self._parse_table(child, table_index, issues))

            for part in sorted(
                name for name in names if re.fullmatch(r"word/(?:header|footer)\d+\.xml", name)
            ):
                try:
                    root = ElementTree.fromstring(package.read(part))
                except ElementTree.ParseError:
                    continue
                for index, paragraph in enumerate(root.findall(f".//{W}p"), start=1):
                    text = _paragraph_text(paragraph)
                    if text:
                        blocks.append(
                            TextBlock(
                                block_id=f"{Path(part).stem}-paragraph-{index}",
                                text=text,
                                normalized_text=" ".join(text.split()),
                                reading_order=len(blocks) + 1,
                                block_type="HEADER" if "header" in part else "FOOTER",
                                extraction_method="DOCX_NATIVE_TEXT",
                                source_fragment_sha256=source_fragment_sha256(text),
                                structure_location=f"{part};paragraph:{index}",
                                coordinate_space=CoordinateSpace.DOCX_STRUCTURE,
                                confidence=1.0,
                            )
                        )

            image_parts: dict[str, set[str]] = {}
            for part in sorted(
                name for name in names if name.endswith(".xml") and name.startswith("word/")
            ):
                rels = _relationships(package, part)
                for relationship_id, target in rels.items():
                    if target.startswith("word/media/") and target in names:
                        image_parts.setdefault(target, set()).add(f"{part}#{relationship_id}")
            captions = [
                block.text
                for block in blocks
                if str(block.style_hint.get("style_name") or "").casefold() in {"caption", "题注"}
            ]
            for image_index, (part, _relationship_refs) in enumerate(
                sorted(image_parts.items()), start=1
            ):
                content = package.read(part)
                processed = preprocess_image(content)
                image_id = f"image-{image_index}"
                preview_ref = f"previews/{image_id}.png"
                resources.append(PreviewResource(preview_ref, "image/png", processed.image_bytes))
                image_block_ids: list[str] = []
                for code, flag, message in (
                    ("PARSE.IMAGE_BLURRY", processed.quality.blurry, "图片清晰度低"),
                    ("PARSE.IMAGE_OVEREXPOSED", processed.quality.overexposed, "图片过曝"),
                    ("PARSE.IMAGE_UNDEREXPOSED", processed.quality.underexposed, "图片欠曝"),
                    ("PARSE.IMAGE_SKEWED", processed.quality.skewed, "图片存在明显倾斜"),
                    (
                        "PARSE.IMAGE_CONTENT_CLIPPED",
                        processed.quality.clipped_content,
                        "图片内容疑似触及边界或被截切",
                    ),
                ):
                    if flag:
                        issues.append(
                            ParseIssue(
                                code,
                                message,
                                IssueSeverity.WARNING,
                                object_id=image_id,
                                location=part,
                            )
                        )
                if processed.quality.low_resolution:
                    issues.append(
                        ParseIssue(
                            "PARSE.IMAGE_LOW_RESOLUTION",
                            "图片短边小于600像素",
                            IssueSeverity.WARNING,
                            object_id=image_id,
                            location=part,
                        )
                    )
                request = OcrRequest(
                    image_bytes=processed.image_bytes,
                    width=processed.width,
                    height=processed.height,
                    languages=context.languages,
                    detect_tables=True,
                    request_id=f"{context.source.source_id}:{image_id}",
                    timeout_seconds=context.ocr_timeout_seconds,
                )
                try:
                    response, _cache_hit = context.ocr_service.recognize(request)
                except OcrProviderError as exc:
                    issues.append(
                        ParseIssue(
                            exc.code,
                            str(exc),
                            IssueSeverity.WARNING,
                            object_id=image_id,
                            location=part,
                            retryable=exc.retryable,
                        )
                    )
                else:
                    inverse = processed.original_to_processed.inverse()
                    image_ocr_blocks: list[TextBlock] = []
                    for ocr_index, ocr_block in enumerate(response.text_blocks, start=1):
                        block_id = f"{image_id}-ocr-{ocr_index}"
                        image_block_ids.append(block_id)
                        original_bbox = inverse.box(ocr_block.bbox)
                        text_block = TextBlock(
                            block_id=block_id,
                            text=ocr_block.text,
                            normalized_text=" ".join(ocr_block.text.split()),
                            reading_order=len(blocks) + 1,
                            block_type=ocr_block.block_type,
                            extraction_method="OCR_IMAGE_REGION",
                            source_fragment_sha256=source_fragment_sha256(
                                [response.raw_response_sha256, ocr_index, ocr_block.text]
                            ),
                            structure_location=f"{part};ocr-block:{ocr_index}",
                            bbox=original_bbox,
                            coordinate_space=CoordinateSpace.IMAGE_PIXELS_TOP_LEFT,
                            page_width=float(processed.original_width),
                            page_height=float(processed.original_height),
                            confidence=ocr_block.confidence,
                            language=ocr_block.language,
                        )
                        blocks.append(text_block)
                        image_ocr_blocks.append(text_block)
                        if ocr_block.confidence < context.low_confidence_threshold:
                            issues.append(
                                ParseIssue(
                                    "PARSE.LOW_TEXT_CONFIDENCE",
                                    f"图片OCR文本块置信度{ocr_block.confidence:.3f}低于阈值",
                                    IssueSeverity.WARNING,
                                    object_id=block_id,
                                    location=part,
                                )
                            )
                    for table_index, table in enumerate(response.tables, start=1):
                        table_id = f"{image_id}-table-{table_index}"
                        table_cells = tuple(
                            ParsedCell(
                                row_index=cell.row_index,
                                column_index=cell.column_index,
                                address=f"R{cell.row_index + 1}C{cell.column_index + 1}",
                                raw_value=cell.text,
                                display_text=cell.text,
                                value_type="EMPTY_STRING" if cell.text == "" else "STRING",
                                source_location=(
                                    f"{part};ocr-table:{table_index};row:{cell.row_index + 1};"
                                    f"column:{cell.column_index + 1}"
                                ),
                                extraction_method="OCR_TABLE",
                                confidence=cell.confidence,
                                row_span=cell.row_span,
                                column_span=cell.column_span,
                                bbox=inverse.box(cell.bbox) if cell.bbox else None,
                                coordinate_space=CoordinateSpace.IMAGE_PIXELS_TOP_LEFT,
                                source_fragment_sha256=source_fragment_sha256(
                                    [
                                        response.raw_response_sha256,
                                        table_index,
                                        cell.row_index,
                                        cell.column_index,
                                        cell.text,
                                    ]
                                ),
                            )
                            for cell in table.cells
                        )
                        tables.append(
                            ParsedTable(
                                table_id=table_id,
                                row_count=table.row_count,
                                column_count=table.column_count,
                                cells=table_cells,
                                extraction_method="OCR_TABLE",
                                confidence=table.confidence,
                                bbox=inverse.box(table.bbox) if table.bbox else None,
                                coordinate_space=CoordinateSpace.IMAGE_PIXELS_TOP_LEFT,
                                metadata={"source_image_id": image_id, "source_part": part},
                            )
                        )
                    if not response.tables:
                        inferred = infer_table_from_blocks(
                            image_ocr_blocks,
                            table_id=f"{image_id}-table-inferred-1",
                            page_number=None,
                            source_prefix=f"{part};ocr-image:{image_index}",
                        )
                        if inferred is not None:
                            tables.append(inferred)
                    ocr_calls.append(
                        {
                            "image_id": image_id,
                            "provider_id": response.provider_id,
                            "model_version": response.model_version,
                            "raw_response_sha256": response.raw_response_sha256,
                            "provider_request_id": response.provider_request_id,
                        }
                    )
                images.append(
                    ImageRegion(
                        image_id=image_id,
                        source_part=part,
                        pixel_width=processed.original_width,
                        pixel_height=processed.original_height,
                        content_sha256=hashlib.sha256(content).hexdigest(),
                        coordinate_space=CoordinateSpace.IMAGE_PIXELS_TOP_LEFT,
                        preview_ref=preview_ref,
                        caption_candidate=captions[image_index - 1]
                        if image_index <= len(captions)
                        else None,
                        ocr_block_ids=tuple(image_block_ids),
                        original_to_processed=processed.original_to_processed.values(),
                        processing_steps=processed.processing_steps,
                    )
                )
            if not blocks and not tables and not images:
                issues.append(
                    ParseIssue(
                        "PARSE.NO_CONTENT", "DOCX没有可解析正文、表格或图片", IssueSeverity.ERROR
                    )
                )
        finally:
            package.close()
        document = ParsedDocument(
            document_id=context.source.source_id,
            source=context.source,
            media_type=context.media_type,
            document_kind="DOCX",
            parser_id=self.reader_id,
            parser_version=self.parser_version,
            page_count=0,
            text_blocks=tuple(blocks),
            tables=tuple(tables),
            images=tuple(images),
            metadata={
                "pagination": "UNAVAILABLE_WITHOUT_DETERMINISTIC_RENDER",
                "main_document_part": "word/document.xml",
                "paragraph_count": paragraph_index,
                "native_table_count": table_index,
                "ocr_calls": ocr_calls,
            },
            issues=tuple(issues),
        )
        return ReaderOutput(document, tuple(resources))

    @staticmethod
    def _parse_table(
        element: ElementTree.Element,
        table_index: int,
        issues: list[ParseIssue],
    ) -> ParsedTable:
        mutable_cells: list[dict[str, object]] = []
        active_vertical: dict[int, int] = {}
        max_column = 0
        rows = element.findall(f"./{W}tr")
        for row_index, row in enumerate(rows):
            column_index = 0
            continued_columns: set[int] = set()
            for cell in row.findall(f"./{W}tc"):
                span_node = cell.find(f"./{W}tcPr/{W}gridSpan")
                column_span = (
                    int(span_node.attrib.get(f"{W}val", "1")) if span_node is not None else 1
                )
                merge_node = cell.find(f"./{W}tcPr/{W}vMerge")
                merge_value = merge_node.attrib.get(f"{W}val") if merge_node is not None else None
                if (
                    merge_node is not None
                    and merge_value != "restart"
                    and column_index in active_vertical
                ):
                    master_index = active_vertical[column_index]
                    mutable_cells[master_index]["row_span"] = (
                        int(mutable_cells[master_index]["row_span"]) + 1
                    )
                    continued_columns.add(column_index)
                else:
                    text = _cell_text(cell)
                    mutable_cells.append(
                        {
                            "row_index": row_index,
                            "column_index": column_index,
                            "address": f"T{table_index}R{row_index + 1}C{column_index + 1}",
                            "raw_value": text,
                            "display_text": text,
                            "value_type": "EMPTY_STRING" if text == "" else "STRING",
                            "source_location": (
                                f"word/document.xml;table:{table_index};row:{row_index + 1};"
                                f"column:{column_index + 1}"
                            ),
                            "extraction_method": "DOCX_NATIVE_TABLE",
                            "confidence": 1.0,
                            "row_span": 1,
                            "column_span": column_span,
                            "coordinate_space": CoordinateSpace.DOCX_STRUCTURE,
                            "source_fragment_sha256": source_fragment_sha256(text),
                        }
                    )
                    if merge_node is not None and merge_value == "restart":
                        active_vertical[column_index] = len(mutable_cells) - 1
                column_index += column_span
                max_column = max(max_column, column_index)
            for column in set(active_vertical) - continued_columns:
                cell_index = active_vertical[column]
                if int(mutable_cells[cell_index]["row_index"]) < row_index:
                    active_vertical.pop(column, None)
        cells = tuple(ParsedCell(**value) for value in mutable_cells)
        if any(cell.row_span > 1 or cell.column_span > 1 for cell in cells):
            issues.append(
                ParseIssue(
                    "PARSE.MERGED_CELL_EXPANDED",
                    f"DOCX表格{table_index}包含合并单元格；解析合同保留主单元格和跨度",
                    IssueSeverity.INFO,
                    object_id=f"table-{table_index}",
                )
            )
        return ParsedTable(
            table_id=f"table-{table_index}",
            row_count=len(rows),
            column_count=max_column,
            cells=cells,
            extraction_method="DOCX_NATIVE_TABLE",
            confidence=0.9,
            coordinate_space=CoordinateSpace.DOCX_STRUCTURE,
            metadata={"legacy_name": f"Table {table_index}", "structure_part": "word/document.xml"},
        )

    def supports(self, path: Path) -> bool:
        return path.suffix.casefold() == ".docx"

    def read(self, path: Path):
        return legacy_read(
            self,
            path,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )


__all__ = ["DocxReader"]

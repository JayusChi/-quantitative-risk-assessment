from __future__ import annotations

import csv
import hashlib
import io
import json
import mimetypes
import os
import re
import stat
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader

INTAKE_RULES_VERSION = "qra.file-intake/1.1.0"
SUPPORTED_SOURCE_SUFFIXES = frozenset(
    {".csv", ".xls", ".xlsx", ".docx", ".pdf", ".png", ".jpg", ".jpeg"}
)
SUPPORTED_UPLOAD_SUFFIXES = SUPPORTED_SOURCE_SUFFIXES | {".zip"}


def _positive_limit(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


MAX_SOURCE_FILES = _positive_limit("QRA_MAX_SOURCE_FILES", 100)
MAX_UPLOAD_FILE_BYTES = _positive_limit("QRA_MAX_UPLOAD_FILE_BYTES", 18 * 1024 * 1024)
MAX_UPLOAD_TOTAL_BYTES = _positive_limit("QRA_MAX_UPLOAD_TOTAL_BYTES", 18 * 1024 * 1024)
MAX_ARCHIVE_MEMBERS = _positive_limit("QRA_MAX_ARCHIVE_MEMBERS", 200)
MAX_ARCHIVE_MEMBER_BYTES = _positive_limit(
    "QRA_MAX_ARCHIVE_MEMBER_BYTES", MAX_UPLOAD_FILE_BYTES
)
MAX_ARCHIVE_EXPANDED_BYTES = _positive_limit(
    "QRA_MAX_ARCHIVE_EXPANDED_BYTES", 100 * 1024 * 1024
)
MAX_ARCHIVE_DEPTH = _positive_limit("QRA_MAX_ARCHIVE_DEPTH", 12)
MAX_ARCHIVE_NAME_LENGTH = _positive_limit("QRA_MAX_ARCHIVE_NAME_LENGTH", 240)
MAX_ARCHIVE_COMPRESSION_RATIO = _positive_limit("QRA_MAX_ARCHIVE_COMPRESSION_RATIO", 100)
MAX_JPEG_TRAILING_BYTES = _positive_limit("QRA_MAX_JPEG_TRAILING_BYTES", 4096)

_PDF = b"%PDF-"
_PNG = b"\x89PNG\r\n\x1a\n"
_OLE = bytes.fromhex("D0CF11E0A1B11AE1")
_ZIP_PREFIXES = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
_DRIVE_PATH = re.compile(r"^[A-Za-z]:($|/)")
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _unsafe_windows_component(value: str) -> bool:
    stem = value.partition(".")[0].casefold().upper()
    return (
        value.endswith((" ", "."))
        or any(character in '<>:"|?*' for character in value)
        or stem in _WINDOWS_RESERVED
    )


@dataclass(frozen=True)
class DetectedFileType:
    type_id: str
    media_type: str
    source_kind: str
    suffixes: tuple[str, ...]


@dataclass(frozen=True)
class IntakeIssue:
    code: str
    message: str
    severity: str = "ERROR"
    relative_path: str | None = None
    blocking: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "relative_path": self.relative_path,
            "blocking": self.blocking,
        }


@dataclass(frozen=True)
class IntakeBatch:
    sources: list[dict[str, Any]]
    issues: list[IntakeIssue]
    file_manifest_sha256: str

    @property
    def ready_count(self) -> int:
        return sum(source["security_status"] == "READY_FOR_PARSE" for source in self.sources)

    @property
    def quarantined_count(self) -> int:
        return sum(source["security_status"] == "QUARANTINED" for source in self.sources)


class IntakeError(ValueError):
    def __init__(self, message: str, issues: list[IntakeIssue] | None = None):
        super().__init__(message)
        self.issues = list(issues or [])


class _DetectionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


FILE_TYPES = {
    "CSV": DetectedFileType("CSV", "text/csv", "TABULAR", (".csv",)),
    "XLS": DetectedFileType("XLS", "application/vnd.ms-excel", "TABULAR", (".xls",)),
    "XLSX": DetectedFileType(
        "XLSX",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "TABULAR",
        (".xlsx",),
    ),
    "DOCX": DetectedFileType(
        "DOCX",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "NATIVE_DOCUMENT",
        (".docx",),
    ),
    "PDF": DetectedFileType("PDF", "application/pdf", "PDF", (".pdf",)),
    "PNG": DetectedFileType("PNG", "image/png", "IMAGE", (".png",)),
    "JPEG": DetectedFileType("JPEG", "image/jpeg", "IMAGE", (".jpg", ".jpeg")),
    "ZIP": DetectedFileType("ZIP", "application/zip", "UNKNOWN", (".zip",)),
}


def normalize_upload_name(value: str) -> str:
    name = str(value).strip()
    if not name or name in {".", ".."}:
        raise IntakeError(
            "源文件名不能为空",
            [IntakeIssue("INTAKE.INVALID_NAME", "源文件名不能为空")],
        )
    if Path(name).name != name or "/" in name or "\\" in name:
        raise IntakeError(
            f"源文件名不能包含路径：{name}",
            [IntakeIssue("INTAKE.INVALID_NAME", "源文件名不能包含路径")],
        )
    if (
        len(name) > 180
        or any(ord(character) < 32 for character in name)
        or _unsafe_windows_component(name)
    ):
        raise IntakeError(
            "源文件名包含控制字符或过长",
            [IntakeIssue("INTAKE.INVALID_NAME", "源文件名包含控制字符或过长")],
        )
    if Path(name).suffix.casefold() not in SUPPORTED_UPLOAD_SUFFIXES:
        allowed = "、".join(sorted(SUPPORTED_UPLOAD_SUFFIXES))
        raise IntakeError(
            f"不支持的源文件类型：{name}；允许{allowed}",
            [
                IntakeIssue(
                    "INTAKE.UNSUPPORTED_EXTENSION",
                    f"不支持扩展名{Path(name).suffix or '(无)'}",
                    relative_path=name,
                )
            ],
        )
    return name


def _read_zip(content: bytes) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(content), mode="r")
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise _DetectionError("INTAKE.INVALID_ZIP", "ZIP容器已损坏或格式无效") from exc


def _zip_type(content: bytes) -> DetectedFileType:
    with _read_zip(content) as archive:
        names = {name.replace("\\", "/") for name in archive.namelist()}
        if "[Content_Types].xml" in names and "xl/workbook.xml" in names:
            return FILE_TYPES["XLSX"]
        if "[Content_Types].xml" in names and "word/document.xml" in names:
            return FILE_TYPES["DOCX"]
    return FILE_TYPES["ZIP"]


def _validate_pdf(content: bytes) -> None:
    try:
        reader = PdfReader(io.BytesIO(content), strict=True)
        if len(reader.pages) < 1:
            raise ValueError("PDF没有页面")
    except Exception as exc:
        raise _DetectionError("INTAKE.INVALID_PDF", "PDF结构损坏或无法读取") from exc


def _validate_image(content: bytes, expected_format: str) -> None:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as image:
                if image.format != expected_format:
                    raise ValueError("图像编码与签名不一致")
                width, height = image.size
                if width <= 0 or height <= 0:
                    raise ValueError("图像尺寸无效")
                image.verify()
            # ``verify`` only checks container structure. Reopen and decode all
            # pixels so a truly truncated JPEG is still rejected even when its
            # header and dimensions look valid.
            with Image.open(io.BytesIO(content)) as image:
                if image.format != expected_format:
                    raise ValueError("图像编码与签名不一致")
                image.load()
    except (
        Image.DecompressionBombError,
        OSError,
        ValueError,
        UnidentifiedImageError,
        Warning,
    ) as exc:
        raise _DetectionError("INTAKE.INVALID_IMAGE", "图像已损坏或无法读取尺寸") from exc


def _jpeg_trailing_byte_count(content: bytes) -> int | None:
    end_marker = content.rfind(b"\xff\xd9")
    return None if end_marker < 0 else len(content) - end_marker - 2


def _jpeg_intake_warning(content: bytes, *, relative_path: str) -> IntakeIssue | None:
    trailing_bytes = _jpeg_trailing_byte_count(content)
    # A small tail is commonly appended by image-sharing software. The image
    # has already passed a full pixel decode and the size is bounded by
    # ``MAX_JPEG_TRAILING_BYTES``, so it is safe and needs no customer-facing
    # warning. Missing EOI remains visible because it is structurally unusual.
    if trailing_bytes is not None:
        return None
    return IntakeIssue(
        "INTAKE.JPEG_EOI_MISSING",
        "JPEG没有结束标记，但已完成全像素安全解码；继续进入解析",
        severity="WARNING",
        relative_path=relative_path,
        blocking=False,
    )


def _decode_csv(content: bytes) -> str:
    if b"\x00" in content:
        raise _DetectionError("INTAKE.INVALID_CSV", "CSV包含NUL字节")
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise _DetectionError("INTAKE.INVALID_CSV", "CSV不是UTF-8或GB18030文本")
    if not text.strip():
        raise _DetectionError("INTAKE.EMPTY_FILE", "CSV没有有效内容")
    controls = sum(ord(character) < 32 and character not in "\t\r\n" for character in text)
    if controls / max(len(text), 1) > 0.01:
        raise _DetectionError("INTAKE.INVALID_CSV", "CSV控制字符比例异常")
    return text


def _validate_csv(content: bytes) -> None:
    text = _decode_csv(content)
    lines = [line for line in text.splitlines() if line.strip()]
    sample = "\n".join(lines[:50])[:65536]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        if len(lines) > 1:
            raise _DetectionError("INTAKE.INVALID_CSV", "CSV分隔符无法识别") from None
        dialect = csv.excel
    try:
        rows = list(csv.reader(io.StringIO(text), dialect=dialect, strict=True))
    except csv.Error as exc:
        raise _DetectionError("INTAKE.INVALID_CSV", "CSV行列结构无法解析") from exc
    widths = [len(row) for row in rows if any(cell.strip() for cell in row)]
    if not widths:
        raise _DetectionError("INTAKE.EMPTY_FILE", "CSV没有有效记录")
    if len(widths) > 1 and (min(widths) < 1 or max(widths) != min(widths)):
        raise _DetectionError("INTAKE.INVALID_CSV", "CSV各行字段数量不一致")


def detect_file_type(content: bytes, *, file_name: str = "") -> DetectedFileType:
    if not content:
        raise _DetectionError("INTAKE.EMPTY_FILE", "文件为空")
    suffix = Path(file_name).suffix.casefold()
    if content.startswith(_PDF):
        _validate_pdf(content)
        return FILE_TYPES["PDF"]
    if content.startswith(_PNG):
        _validate_image(content, "PNG")
        return FILE_TYPES["PNG"]
    if content.startswith(b"\xff\xd8"):
        _validate_image(content, "JPEG")
        trailing_bytes = _jpeg_trailing_byte_count(content)
        if trailing_bytes is not None and trailing_bytes > MAX_JPEG_TRAILING_BYTES:
            raise _DetectionError(
                "INTAKE.INVALID_IMAGE",
                f"JPEG结束标记后的尾随数据超过{MAX_JPEG_TRAILING_BYTES}字节安全限制",
            )
        return FILE_TYPES["JPEG"]
    if content.startswith(_OLE):
        return FILE_TYPES["XLS"]
    if content.startswith(_ZIP_PREFIXES):
        detected = _zip_type(content)
        if suffix == ".xlsx" and detected.type_id != "XLSX":
            raise _DetectionError("INTAKE.INVALID_OOXML", "XLSX缺少约定OOXML部件")
        if suffix == ".docx" and detected.type_id != "DOCX":
            raise _DetectionError("INTAKE.INVALID_OOXML", "DOCX缺少约定OOXML部件")
        return detected
    if suffix != ".csv":
        try:
            _validate_csv(content)
        except _DetectionError:
            pass
        else:
            return FILE_TYPES["CSV"]
    if suffix in {".xlsx", ".docx", ".zip"}:
        raise _DetectionError("INTAKE.INVALID_ZIP", "文件不是有效ZIP/OOXML容器")
    if suffix == ".xls":
        raise _DetectionError("INTAKE.INVALID_XLS", "XLS缺少OLE Compound File签名")
    if suffix == ".pdf":
        raise _DetectionError("INTAKE.INVALID_PDF", "PDF缺少%PDF-签名")
    if suffix in {".png", ".jpg", ".jpeg"}:
        raise _DetectionError("INTAKE.INVALID_IMAGE", "图像签名与扩展名不一致")
    _validate_csv(content)
    return FILE_TYPES["CSV"]


def _declared_mime_matches(declared: str, detected: DetectedFileType) -> bool:
    normalized = declared.partition(";")[0].strip().casefold()
    if not normalized or normalized == "application/octet-stream":
        return True
    aliases = {
        "CSV": {"text/csv", "application/csv", "text/plain", "application/vnd.ms-excel"},
        "JPEG": {"image/jpeg", "image/jpg", "image/pjpeg"},
        "ZIP": {"application/zip", "application/x-zip-compressed"},
    }
    return normalized == detected.media_type.casefold() or normalized in aliases.get(
        detected.type_id, set()
    )


def safe_archive_member(info: zipfile.ZipInfo) -> PurePosixPath:
    raw_name = info.filename.replace("\\", "/")
    if (
        not raw_name
        or raw_name.startswith("/")
        or raw_name.startswith("//")
        or _DRIVE_PATH.match(raw_name)
    ):
        raise IntakeError(
            "ZIP包含绝对路径或空路径",
            [IntakeIssue("INTAKE.ZIP_PATH_TRAVERSAL", "ZIP包含绝对路径或空路径")],
        )
    member = PurePosixPath(raw_name)
    if not member.parts or any(part in {"", ".", ".."} for part in member.parts):
        raise IntakeError(
            "ZIP包含不安全相对路径",
            [IntakeIssue("INTAKE.ZIP_PATH_TRAVERSAL", "ZIP包含不安全相对路径")],
        )
    if any(ord(character) < 32 for character in raw_name):
        raise IntakeError(
            "ZIP成员名包含控制字符",
            [IntakeIssue("INTAKE.ZIP_INVALID_NAME", "ZIP成员名包含控制字符")],
        )
    if any(_unsafe_windows_component(part) for part in member.parts):
        raise IntakeError(
            "ZIP成员名包含Windows保留名称或非法字符",
            [
                IntakeIssue(
                    "INTAKE.ZIP_INVALID_NAME",
                    "ZIP成员名包含Windows保留名称或非法字符",
                )
            ],
        )
    if info.flag_bits & 0x1:
        raise IntakeError(
            "ZIP包含加密成员",
            [IntakeIssue("INTAKE.ZIP_ENCRYPTED", "ZIP包含加密成员")],
        )
    mode = info.external_attr >> 16
    if mode and stat.S_ISLNK(mode):
        raise IntakeError(
            "ZIP包含符号链接",
            [IntakeIssue("INTAKE.ZIP_SYMLINK", "ZIP包含符号链接")],
        )
    if len(member.parts) > MAX_ARCHIVE_DEPTH:
        raise IntakeError(
            "ZIP目录层级超过安全限制",
            [IntakeIssue("INTAKE.ZIP_DEPTH_LIMIT", "ZIP目录层级超过安全限制")],
        )
    if len(raw_name) > MAX_ARCHIVE_NAME_LENGTH or any(
        len(part) > 180 for part in member.parts
    ):
        raise IntakeError(
            "ZIP成员路径过长",
            [IntakeIssue("INTAKE.ZIP_NAME_TOO_LONG", "ZIP成员路径过长")],
        )
    return member


def _new_source(
    *,
    original_file_name: str,
    relative_path: str,
    declared_media_type: str,
    detected: DetectedFileType,
    content: bytes,
    security_status: str,
    issue: IntakeIssue | None = None,
    archive_name: str | None = None,
    archive_member_path: str | None = None,
) -> dict[str, Any]:
    return {
        "id": f"SOURCE-{uuid4()}",
        "file_name": relative_path,
        "relative_path": relative_path,
        "original_file_name": original_file_name,
        "media_type": detected.media_type,
        "declared_media_type": declared_media_type,
        "detected_media_type": detected.media_type,
        "detected_type": detected.type_id,
        "byte_count": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "source_kind": detected.source_kind,
        "security_status": security_status,
        "security_issue_code": issue.code if issue else None,
        "security_issue_message": issue.message if issue else None,
        "duplicate_of_source_id": None,
        "version_group_id": None,
        "archive_name": archive_name,
        "archive_member_path": archive_member_path,
        "content": content,
    }


def _quarantined_source(
    *,
    name: str,
    declared_media_type: str,
    content: bytes,
    issue: IntakeIssue,
    archive_name: str | None = None,
    archive_member_path: str | None = None,
) -> dict[str, Any]:
    suffix = Path(name).suffix.casefold()
    fallback = next(
        (kind for kind in FILE_TYPES.values() if suffix in kind.suffixes),
        DetectedFileType("UNKNOWN", "application/octet-stream", "UNKNOWN", (suffix,)),
    )
    return _new_source(
        original_file_name=Path(name).name,
        relative_path=name,
        declared_media_type=declared_media_type,
        detected=fallback,
        content=content,
        security_status="QUARANTINED",
        issue=issue,
        archive_name=archive_name,
        archive_member_path=archive_member_path,
    )


def _archive_sources(
    archive_name: str,
    declared_media_type: str,
    content: bytes,
    *,
    expanded_budget: int = MAX_ARCHIVE_EXPANDED_BYTES,
    member_budget: int = MAX_ARCHIVE_MEMBERS,
) -> tuple[list[dict[str, Any]], list[IntakeIssue]]:
    issues: list[IntakeIssue] = []
    archive_source = _new_source(
        original_file_name=archive_name,
        relative_path=archive_name,
        declared_media_type=declared_media_type,
        detected=FILE_TYPES["ZIP"],
        content=content,
        security_status="VALIDATED",
    )
    sources = [archive_source]
    with _read_zip(content) as archive:
        members = archive.infolist()
        file_members = [info for info in members if not info.is_dir()]
        effective_member_limit = min(MAX_ARCHIVE_MEMBERS, max(member_budget, 0))
        if len(file_members) > effective_member_limit:
            issue = IntakeIssue(
                "INTAKE.ZIP_MEMBER_LIMIT",
                f"ZIP文件成员使任务超过{MAX_ARCHIVE_MEMBERS}个展开成员限制",
                relative_path=archive_name,
            )
            archive_source.update(
                security_status="QUARANTINED",
                security_issue_code=issue.code,
                security_issue_message=issue.message,
            )
            return sources, [issue]
        expanded = sum(info.file_size for info in file_members)
        effective_expanded_limit = min(
            MAX_ARCHIVE_EXPANDED_BYTES, max(expanded_budget, 0)
        )
        if expanded > effective_expanded_limit:
            issue = IntakeIssue(
                "INTAKE.ZIP_EXPANDED_LIMIT",
                "ZIP总展开量超过安全限制",
                relative_path=archive_name,
            )
            archive_source.update(
                security_status="QUARANTINED",
                security_issue_code=issue.code,
                security_issue_message=issue.message,
            )
            return sources, [issue]

        used_paths: set[str] = set()
        validated_members: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
        try:
            for info in members:
                member = safe_archive_member(info)
                if info.is_dir():
                    continue
                relative = member.as_posix()
                path_key = relative.casefold()
                if path_key in used_paths:
                    raise IntakeError(
                        "ZIP包含大小写不敏感的重复路径",
                        [
                            IntakeIssue(
                                "INTAKE.ZIP_DUPLICATE_PATH",
                                "ZIP包含大小写不敏感的重复路径",
                                relative_path=relative,
                            )
                        ],
                    )
                used_paths.add(path_key)
                if info.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                    raise IntakeError(
                        "ZIP单个成员超过安全限制",
                        [
                            IntakeIssue(
                                "INTAKE.ZIP_MEMBER_TOO_LARGE",
                                "ZIP单个成员超过安全限制",
                                relative_path=relative,
                            )
                        ],
                    )
                ratio = info.file_size / max(info.compress_size, 1)
                if ratio > MAX_ARCHIVE_COMPRESSION_RATIO:
                    raise IntakeError(
                        "ZIP成员压缩比超过安全限制",
                        [
                            IntakeIssue(
                                "INTAKE.ZIP_COMPRESSION_RATIO",
                                "ZIP成员压缩比超过安全限制",
                                relative_path=relative,
                            )
                        ],
                    )
                validated_members.append((info, member))
        except IntakeError as exc:
            issue = exc.issues[0]
            archive_source.update(
                security_status="QUARANTINED",
                security_issue_code=issue.code,
                security_issue_message=issue.message,
            )
            return sources, list(exc.issues)

        for info, member in validated_members:
            relative = member.as_posix()
            suffix = Path(member.name).suffix.casefold()
            try:
                data = archive.read(info)
            except (RuntimeError, OSError, zipfile.BadZipFile):
                issue = IntakeIssue(
                    "INTAKE.ZIP_INTEGRITY_FAILED",
                    "ZIP成员CRC或长度校验失败",
                    relative_path=relative,
                )
                archive_source.update(
                    security_status="QUARANTINED",
                    security_issue_code=issue.code,
                    security_issue_message=issue.message,
                )
                return [archive_source], [issue]
            if len(data) != info.file_size:
                issue = IntakeIssue(
                    "INTAKE.ZIP_INTEGRITY_FAILED",
                    "ZIP成员长度校验失败",
                    relative_path=relative,
                )
                archive_source.update(
                    security_status="QUARANTINED",
                    security_issue_code=issue.code,
                    security_issue_message=issue.message,
                )
                return [archive_source], [issue]
            guessed_mime = mimetypes.guess_type(member.name)[0] or "application/octet-stream"
            if suffix == ".zip":
                issue = IntakeIssue(
                    "INTAKE.NESTED_ARCHIVE",
                    "测试版不递归展开嵌套压缩包",
                    relative_path=relative,
                )
                sources.append(
                    _quarantined_source(
                        name=relative,
                        declared_media_type=guessed_mime,
                        content=data,
                        issue=issue,
                        archive_name=archive_name,
                        archive_member_path=relative,
                    )
                )
                sources[-1]["file_name"] = f"{archive_name}!/{relative}"
                issues.append(issue)
                continue
            if suffix not in SUPPORTED_SOURCE_SUFFIXES:
                issue = IntakeIssue(
                    "INTAKE.UNSUPPORTED_MEMBER",
                    "ZIP成员格式不受支持",
                    relative_path=relative,
                )
                sources.append(
                    _quarantined_source(
                        name=relative,
                        declared_media_type=guessed_mime,
                        content=data,
                        issue=issue,
                        archive_name=archive_name,
                        archive_member_path=relative,
                    )
                )
                sources[-1]["file_name"] = f"{archive_name}!/{relative}"
                issues.append(issue)
                continue
            try:
                detected = detect_file_type(data, file_name=member.name)
                if suffix not in detected.suffixes:
                    raise _DetectionError(
                        "INTAKE.TYPE_MISMATCH", "ZIP成员扩展名与实际类型不一致"
                    )
                source = _new_source(
                    original_file_name=member.name,
                    relative_path=relative,
                    declared_media_type=guessed_mime,
                    detected=detected,
                    content=data,
                    security_status="READY_FOR_PARSE",
                    archive_name=archive_name,
                    archive_member_path=relative,
                )
                source["file_name"] = f"{archive_name}!/{relative}"
                sources.append(source)
                if detected.type_id == "JPEG":
                    warning = _jpeg_intake_warning(data, relative_path=relative)
                    if warning is not None:
                        issues.append(warning)
            except _DetectionError as exc:
                issue = IntakeIssue(exc.code, str(exc), relative_path=relative)
                sources.append(
                    _quarantined_source(
                        name=relative,
                        declared_media_type=guessed_mime,
                        content=data,
                        issue=issue,
                        archive_name=archive_name,
                        archive_member_path=relative,
                    )
                )
                sources[-1]["file_name"] = f"{archive_name}!/{relative}"
                issues.append(issue)
    if len(sources) == 1:
        issue = IntakeIssue(
            "INTAKE.ZIP_NO_FILE_MEMBERS",
            "ZIP资料包没有可登记的文件成员",
            relative_path=archive_name,
        )
        archive_source.update(
            security_status="QUARANTINED",
            security_issue_code=issue.code,
            security_issue_message=issue.message,
        )
        issues.append(issue)
    return sources, issues


def _mark_duplicate_and_versions(
    sources: list[dict[str, Any]], issues: list[IntakeIssue]
) -> None:
    hashes: dict[str, str] = {}
    name_versions: dict[str, dict[str, Any]] = {}
    ordered_sources = sorted(
        sources,
        key=lambda source: (
            str(source.get("sha256") or ""),
            str(source.get("archive_name") or "").casefold(),
            str(source.get("relative_path") or source.get("file_name") or "").casefold(),
        ),
    )
    for source in ordered_sources:
        if source["security_status"] != "READY_FOR_PARSE":
            continue
        content_hash = str(source["sha256"])
        if content_hash in hashes:
            source["security_status"] = "DUPLICATE"
            source["duplicate_of_source_id"] = hashes[content_hash]
            issue = IntakeIssue(
                "INTAKE.DUPLICATE_FOUND",
                "同一任务存在完全相同内容，保留来源但不重复解析",
                severity="INFO",
                relative_path=str(source["relative_path"]),
                blocking=False,
            )
            issues.append(issue)
            continue
        hashes[content_hash] = str(source["id"])
        version_key = Path(str(source["relative_path"])).name.casefold()
        previous = name_versions.get(version_key)
        if previous is not None and previous["sha256"] != content_hash:
            group_id = previous.get("version_group_id") or (
                "VERSION-"
                + hashlib.sha256(version_key.encode("utf-8")).hexdigest()[:20]
            )
            previous["version_group_id"] = group_id
            source["version_group_id"] = group_id
            issues.append(
                IntakeIssue(
                    "INTAKE.POSSIBLE_NEW_VERSION",
                    "检测到同名异哈希资料，已归入同一版本组",
                    severity="WARNING",
                    relative_path=str(source["relative_path"]),
                    blocking=False,
                )
            )
        else:
            name_versions[version_key] = source


def file_manifest_sha256(sources: list[dict[str, Any]]) -> str:
    rows = sorted(
        (
            {
                "relative_path": str(source["relative_path"]),
                "sha256": str(source["sha256"]),
                "byte_count": int(source["byte_count"]),
                "detected_media_type": str(source["detected_media_type"]),
                "security_status": str(source["security_status"]),
                "archive_name": source.get("archive_name"),
                "archive_member_path": source.get("archive_member_path"),
            }
            for source in sources
        ),
        key=lambda row: (
            row["relative_path"].casefold(),
            row["sha256"],
            str(row["archive_name"] or "").casefold(),
        ),
    )
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def intake_files(files: list[dict[str, Any]]) -> IntakeBatch:
    if not files:
        raise IntakeError(
            "至少上传一个源文件或ZIP资料包",
            [IntakeIssue("INTAKE.NO_FILES", "至少上传一个源文件或ZIP资料包")],
        )
    if len(files) > MAX_SOURCE_FILES:
        raise IntakeError(
            f"一次最多上传{MAX_SOURCE_FILES}个源文件",
            [IntakeIssue("INTAKE.FILE_COUNT_LIMIT", "上传文件数量超过限制")],
        )

    prepared: list[tuple[str, str, bytes]] = []
    top_names: set[str] = set()
    total_bytes = 0
    for item in files:
        name = normalize_upload_name(str(item.get("file_name") or ""))
        if name.casefold() in top_names:
            raise IntakeError(
                f"源文件名重复：{name}",
                [
                    IntakeIssue(
                        "INTAKE.DUPLICATE_PATH",
                        "顶层源文件名大小写不敏感重复",
                        relative_path=name,
                    )
                ],
            )
        top_names.add(name.casefold())
        content = item.get("content")
        if not isinstance(content, bytes):
            raise IntakeError(
                f"源文件内容必须是字节：{name}",
                [IntakeIssue("INTAKE.INVALID_CONTENT", "源文件内容必须是字节")],
            )
        if len(content) > MAX_UPLOAD_FILE_BYTES:
            raise IntakeError(
                f"源文件超过{MAX_UPLOAD_FILE_BYTES}字节限制：{name}",
                [
                    IntakeIssue(
                        "INTAKE.FILE_TOO_LARGE",
                        "源文件超过单文件大小限制",
                        relative_path=name,
                    )
                ],
            )
        total_bytes += len(content)
        if total_bytes > MAX_UPLOAD_TOTAL_BYTES:
            raise IntakeError(
                "本批源文件合计超过安全限制",
                [IntakeIssue("INTAKE.TASK_TOO_LARGE", "任务上传总量超过限制")],
            )
        declared = str(
            item.get("media_type")
            or mimetypes.guess_type(name)[0]
            or "application/octet-stream"
        )
        prepared.append((name, declared, content))

    sources: list[dict[str, Any]] = []
    issues: list[IntakeIssue] = []
    expanded_archive_bytes = 0
    expanded_member_count = 0
    for name, declared, content in prepared:
        try:
            detected = detect_file_type(content, file_name=name)
            suffix = Path(name).suffix.casefold()
            if suffix not in detected.suffixes:
                issue = IntakeIssue(
                    "INTAKE.TYPE_MISMATCH",
                    f"扩展名{suffix}与检测类型{detected.type_id}不一致",
                    relative_path=name,
                )
                sources.append(
                    _quarantined_source(
                        name=name,
                        declared_media_type=declared,
                        content=content,
                        issue=issue,
                    )
                )
                issues.append(issue)
                continue
            if detected.type_id == "ZIP":
                archive_rows, archive_issues = _archive_sources(
                    name,
                    declared,
                    content,
                    expanded_budget=MAX_ARCHIVE_EXPANDED_BYTES - expanded_archive_bytes,
                    member_budget=MAX_ARCHIVE_MEMBERS - expanded_member_count,
                )
                sources.extend(archive_rows)
                issues.extend(archive_issues)
                member_rows = archive_rows[1:]
                expanded_archive_bytes += sum(
                    int(source["byte_count"]) for source in member_rows
                )
                expanded_member_count += len(member_rows)
                continue
            source = _new_source(
                original_file_name=name,
                relative_path=name,
                declared_media_type=declared,
                detected=detected,
                content=content,
                security_status="READY_FOR_PARSE",
            )
            sources.append(source)
            if detected.type_id == "JPEG":
                warning = _jpeg_intake_warning(content, relative_path=name)
                if warning is not None:
                    issues.append(warning)
            if not _declared_mime_matches(declared, detected):
                issues.append(
                    IntakeIssue(
                        "INTAKE.MIME_MISMATCH",
                        "浏览器声明MIME与检测类型不一致；已按实际签名登记",
                        severity="WARNING",
                        relative_path=name,
                        blocking=False,
                    )
                )
        except _DetectionError as exc:
            issue = IntakeIssue(exc.code, str(exc), relative_path=name)
            sources.append(
                _quarantined_source(
                    name=name,
                    declared_media_type=declared,
                    content=content,
                    issue=issue,
                )
            )
            issues.append(issue)

    _mark_duplicate_and_versions(sources, issues)
    return IntakeBatch(
        sources=sources,
        issues=issues,
        file_manifest_sha256=file_manifest_sha256(sources),
    )


__all__ = [
    "DetectedFileType",
    "FILE_TYPES",
    "INTAKE_RULES_VERSION",
    "IntakeBatch",
    "IntakeError",
    "IntakeIssue",
    "MAX_ARCHIVE_COMPRESSION_RATIO",
    "MAX_ARCHIVE_DEPTH",
    "MAX_ARCHIVE_EXPANDED_BYTES",
    "MAX_ARCHIVE_MEMBER_BYTES",
    "MAX_ARCHIVE_MEMBERS",
    "MAX_JPEG_TRAILING_BYTES",
    "MAX_SOURCE_FILES",
    "MAX_UPLOAD_FILE_BYTES",
    "MAX_UPLOAD_TOTAL_BYTES",
    "SUPPORTED_SOURCE_SUFFIXES",
    "SUPPORTED_UPLOAD_SUFFIXES",
    "detect_file_type",
    "file_manifest_sha256",
    "intake_files",
    "normalize_upload_name",
    "safe_archive_member",
]

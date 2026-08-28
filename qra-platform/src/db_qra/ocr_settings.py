"""Encrypted local persistence for OCR provider configuration.

The API key is protected with Windows DPAPI in current-user scope.  Provider
metadata is stored next to the selected SQLite database so a normal server
restart can restore OCR without putting secrets in source control or commands.
"""

from __future__ import annotations

import base64
import binascii
import csv
import ctypes
import io
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

SETTINGS_CONTRACT = "qra.ocr-settings/1.0.0"
PROTECTION_SCHEME = "WINDOWS_DPAPI_CURRENT_USER"
MAX_CONFIG_CSV_BYTES = 64 * 1024
DEFAULT_OCR_TIMEOUT_SECONDS = 120
OCR_MODEL_OPTIONS = (
    ("qwen3.5-ocr", "Qwen3.5-OCR（专用高精OCR，推荐）"),
    ("qwen3.8-max", "Qwen3.8-Max（多模态通用识别）"),
    ("qwen3.7-max", "Qwen3.7-Max（多模态通用识别）"),
    ("qwen3.7-max-2026-06-08", "Qwen3.7-Max-2026-06-08（固定版本）"),
)
SUPPORTED_OCR_MODELS = tuple(model_id for model_id, _label in OCR_MODEL_OPTIONS)
_DPAPI_ENTROPY = b"qra-platform:bailian-ocr:v1"
_MODEL_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,100}")
_HOST_PATTERN = re.compile(r"[A-Za-z0-9.-]{1,253}")


@dataclass(frozen=True)
class BailianOcrSettings:
    api_key: str = field(repr=False)
    api_host: str
    dashscope_url: str
    openai_base_url: str
    ocr_model_version: str = "qwen3.5-ocr"
    vision_model_version: str = "qwen3.7-max"
    ocr_timeout_seconds: int = DEFAULT_OCR_TIMEOUT_SECONDS
    workspace_name: str | None = None
    workspace_id: str | None = None

    def public_metadata(self) -> dict[str, object]:
        return {
            "provider": "aliyun-bailian",
            "api_host": self.api_host,
            "dashscope_url": self.dashscope_url,
            "openai_base_url": self.openai_base_url,
            "ocr_model_version": self.ocr_model_version,
            "vision_model_version": self.vision_model_version,
            "ocr_timeout_seconds": self.ocr_timeout_seconds,
            "workspace_name": self.workspace_name,
            "key_stored": True,
            "protection": PROTECTION_SCHEME,
        }


def _validated_model(value: str, label: str) -> str:
    model = value.strip()
    if not _MODEL_PATTERN.fullmatch(model):
        raise ValueError(f"{label}名称格式无效")
    return model


def _validated_ocr_model(value: str) -> str:
    requested = _validated_model(value, "OCR模型")
    canonical = {model.casefold(): model for model in SUPPORTED_OCR_MODELS}
    selected = canonical.get(requested.casefold())
    if selected is None:
        raise ValueError("OCR模型不在当前业务空间允许的四个模型中")
    return selected


def _validated_timeout(value: object) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("OCR超时必须是整数秒") from exc
    if not 30 <= timeout <= 600:
        raise ValueError("OCR超时必须在30至600秒之间")
    return timeout


def _environment_timeout_seconds() -> int:
    try:
        return _validated_timeout(
            os.environ.get("QRA_OCR_TIMEOUT_SECONDS", DEFAULT_OCR_TIMEOUT_SECONDS)
        )
    except ValueError:
        return DEFAULT_OCR_TIMEOUT_SECONDS


def public_ocr_model_options() -> list[dict[str, object]]:
    return [
        {
            "id": model_id,
            "label": label,
            "recommended": model_id == "qwen3.5-ocr",
            "mode": "STRUCTURED_OCR" if model_id == "qwen3.5-ocr" else "VISION_PROMPT",
        }
        for model_id, label in OCR_MODEL_OPTIONS
    ]


def _validated_url(value: str, *, host: str, suffix: str, label: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.hostname.casefold() != host.casefold()
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port not in (None, 443)
        or parsed.path.rstrip("/") != suffix
    ):
        raise ValueError(f"{label}必须是API Host下以{suffix}结尾的HTTPS地址")
    return normalized


def validate_bailian_settings(
    *,
    api_key: str,
    api_host: str,
    dashscope_url: str,
    openai_base_url: str,
    ocr_model_version: str = "qwen3.5-ocr",
    vision_model_version: str = "qwen3.7-max",
    ocr_timeout_seconds: int = DEFAULT_OCR_TIMEOUT_SECONDS,
    workspace_name: str | None = None,
    workspace_id: str | None = None,
) -> BailianOcrSettings:
    key = api_key.strip()
    if not 16 <= len(key) <= 512 or any(ord(character) < 33 for character in key):
        raise ValueError("API Key格式或长度无效")
    host = api_host.strip().casefold()
    if not _HOST_PATTERN.fullmatch(host) or not host.endswith(".aliyuncs.com"):
        raise ValueError("API Host格式无效")
    safe_workspace_name = workspace_name.strip()[:160] if workspace_name else None
    safe_workspace_id = workspace_id.strip()[:160] if workspace_id else None
    return BailianOcrSettings(
        api_key=key,
        api_host=host,
        dashscope_url=_validated_url(
            dashscope_url,
            host=host,
            suffix="/api/v1",
            label="DashScope地址",
        ),
        openai_base_url=_validated_url(
            openai_base_url,
            host=host,
            suffix="/compatible-mode/v1",
            label="OpenAI兼容地址",
        ),
        ocr_model_version=_validated_ocr_model(ocr_model_version),
        vision_model_version=_validated_model(vision_model_version, "视觉模型"),
        ocr_timeout_seconds=_validated_timeout(ocr_timeout_seconds),
        workspace_name=safe_workspace_name or None,
        workspace_id=safe_workspace_id or None,
    )


def parse_bailian_config_csv(
    csv_text: str,
    *,
    ocr_model_version: str = "qwen3.5-ocr",
    vision_model_version: str = "qwen3.7-max",
    ocr_timeout_seconds: int = DEFAULT_OCR_TIMEOUT_SECONDS,
) -> BailianOcrSettings:
    if len(csv_text.encode("utf-8")) > MAX_CONFIG_CSV_BYTES:
        raise ValueError("OCR配置CSV不能超过64 KB")
    try:
        rows = list(csv.reader(io.StringIO(csv_text.lstrip("\ufeff"))))
    except csv.Error as exc:
        raise ValueError("OCR配置CSV格式无效") from exc
    settings: dict[str, str] = {}
    for row in rows:
        if len(row) < 2:
            continue
        name = row[0].strip().lstrip("\ufeff")
        if name:
            settings[name.casefold()] = row[1].strip()
    required = {
        "apikey": "apiKey",
        "apihost": "apiHost",
        "openaicompatible": "openAiCompatible",
        "dashscope": "dashScope",
    }
    missing = [label for key, label in required.items() if not settings.get(key)]
    if missing:
        raise ValueError("OCR配置CSV缺少：" + "、".join(missing))
    return validate_bailian_settings(
        api_key=settings["apikey"],
        api_host=settings["apihost"],
        dashscope_url=settings["dashscope"],
        openai_base_url=settings["openaicompatible"],
        ocr_model_version=ocr_model_version,
        vision_model_version=vision_model_version,
        ocr_timeout_seconds=ocr_timeout_seconds,
        workspace_name=settings.get("workspacename"),
        workspace_id=settings.get("workspaceid"),
    )


class _DataBlob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(value: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(value)
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    return _DataBlob(len(value), pointer), buffer


def _dpapi_transform(value: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise RuntimeError("持久化OCR密钥需要Windows DPAPI")
    crypt32 = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
    kernel32 = ctypes.WinDLL("Kernel32.dll", use_last_error=True)
    input_blob, input_buffer = _blob(value)
    entropy_blob, entropy_buffer = _blob(_DPAPI_ENTROPY)
    output_blob = _DataBlob()
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    if protect:
        operation = crypt32.CryptProtectData
        operation.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.c_wchar_p,
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(_DataBlob),
        ]
        arguments = (
            ctypes.byref(input_blob),
            "QRA Alibaba Bailian OCR API Key",
            ctypes.byref(entropy_blob),
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )
    else:
        operation = crypt32.CryptUnprotectData
        operation.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.POINTER(_DataBlob),
        ]
        arguments = (
            ctypes.byref(input_blob),
            None,
            ctypes.byref(entropy_blob),
            None,
            None,
            flags,
            ctypes.byref(output_blob),
        )
    operation.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    # Keep the backing buffers alive until the native operation completes.
    _ = input_buffer, entropy_buffer
    if not operation(*arguments):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output_blob.data, output_blob.size)
    finally:
        kernel32.LocalFree(ctypes.cast(output_blob.data, ctypes.c_void_p))


def protect_secret(value: str) -> str:
    encrypted = _dpapi_transform(value.encode("utf-8"), protect=True)
    return base64.b64encode(encrypted).decode("ascii")


def unprotect_secret(value: str) -> str:
    try:
        encrypted = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise ValueError("加密OCR密钥格式无效") from exc
    try:
        return _dpapi_transform(encrypted, protect=False).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("加密OCR密钥无法解码") from exc


class OcrSettingsStore:
    def __init__(
        self,
        path: Path,
        *,
        protector: Callable[[str], str] = protect_secret,
        unprotector: Callable[[str], str] = unprotect_secret,
    ):
        self.path = path.resolve()
        self._protector = protector
        self._unprotector = unprotector

    def save(self, settings: BailianOcrSettings) -> None:
        payload = {
            "contract": SETTINGS_CONTRACT,
            "provider": "aliyun-bailian",
            "protection": PROTECTION_SCHEME,
            "encrypted_api_key": self._protector(settings.api_key),
            "api_host": settings.api_host,
            "dashscope_url": settings.dashscope_url,
            "openai_base_url": settings.openai_base_url,
            "ocr_model_version": settings.ocr_model_version,
            "vision_model_version": settings.vision_model_version,
            "ocr_timeout_seconds": settings.ocr_timeout_seconds,
            "workspace_name": settings.workspace_name,
            "workspace_id": settings.workspace_id,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def load(self) -> BailianOcrSettings | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("OCR持久化配置损坏或无法读取") from exc
        if not isinstance(payload, dict) or payload.get("contract") != SETTINGS_CONTRACT:
            raise ValueError("OCR持久化配置版本不受支持")
        if payload.get("protection") != PROTECTION_SCHEME:
            raise ValueError("OCR密钥保护方式不受支持")
        encrypted_key = payload.get("encrypted_api_key")
        if not isinstance(encrypted_key, str):
            raise ValueError("OCR持久化配置缺少加密密钥")
        return validate_bailian_settings(
            api_key=self._unprotector(encrypted_key),
            api_host=str(payload.get("api_host") or ""),
            dashscope_url=str(payload.get("dashscope_url") or ""),
            openai_base_url=str(payload.get("openai_base_url") or ""),
            ocr_model_version=str(payload.get("ocr_model_version") or "qwen3.5-ocr"),
            vision_model_version=str(payload.get("vision_model_version") or "qwen3.7-max"),
            ocr_timeout_seconds=payload.get(
                "ocr_timeout_seconds", DEFAULT_OCR_TIMEOUT_SECONDS
            ),
            workspace_name=(
                str(payload["workspace_name"]) if payload.get("workspace_name") else None
            ),
            workspace_id=(str(payload["workspace_id"]) if payload.get("workspace_id") else None),
        )


def settings_path_for_database(database_path: Path) -> Path:
    configured = os.environ.get("QRA_OCR_SETTINGS_PATH", "").strip()
    return (
        Path(configured).resolve()
        if configured
        else database_path.resolve().parent / "ocr-settings.json"
    )


def environment_ocr_configured() -> bool:
    return (
        os.environ.get("QRA_OCR_PROVIDER", "").strip().casefold()
        in {"aliyun", "aliyun-bailian", "bailian"}
        and bool(os.environ.get("QRA_ALIYUN_DASHSCOPE_URL", "").strip())
        and bool(os.environ.get("QRA_ALIYUN_API_KEY", "").strip())
    )


def apply_ocr_settings(settings: BailianOcrSettings, *, source: str) -> None:
    os.environ.update(
        {
            "QRA_OCR_PROVIDER": "aliyun-bailian",
            "QRA_ALIYUN_API_KEY": settings.api_key,
            "QRA_ALIYUN_DASHSCOPE_URL": settings.dashscope_url,
            "QRA_ALIYUN_OPENAI_BASE_URL": settings.openai_base_url,
            "QRA_OCR_MODEL_VERSION": settings.ocr_model_version,
            "QRA_VISION_MODEL_VERSION": settings.vision_model_version,
            "QRA_OCR_TIMEOUT_SECONDS": str(settings.ocr_timeout_seconds),
            "QRA_OCR_SETTINGS_SOURCE": source,
        }
    )


def load_ocr_settings_into_process(
    store: OcrSettingsStore, *, overwrite_environment: bool = False
) -> BailianOcrSettings | None:
    if environment_ocr_configured() and not overwrite_environment:
        return None
    settings = store.load()
    if settings is not None:
        apply_ocr_settings(settings, source="encrypted-store")
    return settings


def ocr_settings_status(store: OcrSettingsStore) -> dict[str, object]:
    active = environment_ocr_configured()
    persisted = store.path.exists()
    source = os.environ.get("QRA_OCR_SETTINGS_SOURCE") or (
        "environment" if active else "none"
    )
    metadata: dict[str, object] = {}
    persisted_usable = persisted
    reimport_required = False
    persisted_settings: BailianOcrSettings | None = None
    if persisted and (source == "encrypted-store" or not active):
        try:
            persisted_settings = store.load()
        except (OSError, RuntimeError, ValueError):
            persisted_usable = False
            reimport_required = True
    if persisted_settings is not None:
        metadata = persisted_settings.public_metadata()
    elif active:
        dashscope_url = os.environ.get("QRA_ALIYUN_DASHSCOPE_URL", "")
        metadata = {
            "provider": "aliyun-bailian",
            "api_host": urlsplit(dashscope_url).hostname,
            "dashscope_url": dashscope_url,
            "openai_base_url": os.environ.get("QRA_ALIYUN_OPENAI_BASE_URL"),
            "ocr_model_version": os.environ.get("QRA_OCR_MODEL_VERSION"),
            "vision_model_version": os.environ.get("QRA_VISION_MODEL_VERSION"),
            "ocr_timeout_seconds": _environment_timeout_seconds(),
            "workspace_name": None,
            "key_stored": False,
            "protection": "PROCESS_ENVIRONMENT_ONLY",
        }
    status = {
        "configured": active,
        "persisted": persisted,
        "persisted_usable": persisted_usable,
        "reimport_required": reimport_required,
        "source": source,
        "available_models": public_ocr_model_options(),
        **metadata,
    }
    if reimport_required:
        status["status_issue"] = "OCR_SETTINGS_REIMPORT_REQUIRED"
    return status


__all__ = [
    "BailianOcrSettings",
    "DEFAULT_OCR_TIMEOUT_SECONDS",
    "MAX_CONFIG_CSV_BYTES",
    "OcrSettingsStore",
    "OCR_MODEL_OPTIONS",
    "PROTECTION_SCHEME",
    "SETTINGS_CONTRACT",
    "SUPPORTED_OCR_MODELS",
    "apply_ocr_settings",
    "environment_ocr_configured",
    "load_ocr_settings_into_process",
    "ocr_settings_status",
    "parse_bailian_config_csv",
    "protect_secret",
    "public_ocr_model_options",
    "settings_path_for_database",
    "unprotect_secret",
    "validate_bailian_settings",
]

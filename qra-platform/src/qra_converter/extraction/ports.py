"""Vendor-neutral, tool-free model extraction port with bounded retry policy."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from threading import BoundedSemaphore, Event, Thread
from typing import Any, Protocol
from uuid import uuid4

from ..model_audit import ModelAuditCallback, sanitized_error_message


@dataclass(frozen=True, slots=True)
class ExtractionRequest:
    task_type: str
    request_id: str
    system_policy_version: str
    prompt_template_version: str
    schema: dict[str, Any]
    field_subset: tuple[str, ...]
    document_blocks: tuple[dict[str, Any], ...]
    instructions: str = ""
    field_definitions: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    entity_context: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    timeout_seconds: float = 30.0
    repair_of_request_id: str | None = None
    job_id: str | None = None
    parent_call_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExtractionResponse:
    provider_id: str
    model_id: str
    model_version: str
    structured_output: dict[str, Any]
    usage: dict[str, Any] = field(default_factory=dict)
    finish_reason: str = "stop"
    provider_request_id: str | None = None
    raw_response_sha256: str = ""

    def finalized(self) -> ExtractionResponse:
        if self.raw_response_sha256:
            return self
        raw = json.dumps(
            self.structured_output,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return ExtractionResponse(
            provider_id=self.provider_id,
            model_id=self.model_id,
            model_version=self.model_version,
            structured_output=self.structured_output,
            usage=self.usage,
            finish_reason=self.finish_reason,
            provider_request_id=self.provider_request_id,
            raw_response_sha256=hashlib.sha256(raw).hexdigest(),
        )


class ProviderCallError(RuntimeError):
    def __init__(self, message: str, *, code: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ExtractionProvider(Protocol):
    provider_id: str

    def extract(self, request: ExtractionRequest) -> ExtractionResponse: ...


class ProviderExecutor:
    """Apply retry limits without granting a provider tools, paths, or workflow control."""

    def __init__(
        self,
        provider: ExtractionProvider,
        *,
        max_retries: int = 2,
        max_response_bytes: int = 2_000_000,
        retry_delay_seconds: float = 0.0,
        max_concurrency: int = 4,
        audit_callback: ModelAuditCallback | None = None,
    ) -> None:
        if max_retries < 0 or max_retries > 8:
            raise ValueError("max_retries必须在0到8之间")
        self.provider = provider
        self.max_retries = max_retries
        self.max_response_bytes = max_response_bytes
        self.retry_delay_seconds = max(0.0, retry_delay_seconds)
        if max_concurrency < 1 or max_concurrency > 64:
            raise ValueError("max_concurrency必须在1到64之间")
        self._slots = BoundedSemaphore(max_concurrency)
        self.audit_callback = audit_callback

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _request_bytes(self, request: ExtractionRequest) -> bytes:
        builder = getattr(self.provider, "build_request_bytes", None)
        if callable(builder):
            return bytes(builder(request))
        return json.dumps(
            request.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    def request_byte_count(self, request: ExtractionRequest) -> int:
        return len(self._request_bytes(request))

    def _audit(self, value: dict[str, Any]) -> None:
        if self.audit_callback is not None:
            self.audit_callback(value)

    def record_local_skip(
        self,
        request: ExtractionRequest,
        *,
        error_code: str,
        input_byte_count: int,
    ) -> None:
        now = self._now()
        self._audit(
            {
                "id": f"MCALL-{uuid4()}",
                "job_id": request.job_id,
                "call_kind": "EXTRACTION",
                "task_type": request.task_type,
                "logical_request_id": request.request_id,
                "parent_call_id": request.parent_call_id,
                "attempt_number": 0,
                "provider_id": self.provider.provider_id,
                "model_version": getattr(self.provider, "model_version", None),
                "status": "SKIPPED",
                "started_at": now,
                "finished_at": now,
                "elapsed_ms": 0,
                "input_sha256": hashlib.sha256(
                    json.dumps(request.to_dict(), default=str).encode()
                ).hexdigest(),
                "input_byte_count": input_byte_count,
                "retryable": False,
                "error_code": error_code,
                "sanitized_error_message": "请求在本地最终序列化预算检查中被拒绝",
            }
        )

    def _extract_with_timeout(
        self, request: ExtractionRequest, *, attempt_number: int
    ) -> ExtractionResponse:
        call_id = f"MCALL-{uuid4()}"
        started_at = self._now()
        started = time.perf_counter()
        try:
            request_bytes = self._request_bytes(request)
        except ProviderCallError as exc:
            self._audit(
                {
                    "id": call_id,
                    "job_id": request.job_id,
                    "call_kind": "EXTRACTION",
                    "task_type": request.task_type,
                    "logical_request_id": request.request_id,
                    "parent_call_id": request.parent_call_id,
                    "attempt_number": attempt_number,
                    "provider_id": self.provider.provider_id,
                    "model_version": getattr(self.provider, "model_version", None),
                    "status": "SKIPPED",
                    "started_at": started_at,
                    "finished_at": self._now(),
                    "elapsed_ms": 0,
                    "input_sha256": hashlib.sha256(
                        json.dumps(request.to_dict(), default=str).encode()
                    ).hexdigest(),
                    "input_byte_count": 0,
                    "retryable": exc.retryable,
                    "error_code": exc.code,
                    "sanitized_error_message": sanitized_error_message(exc),
                }
            )
            raise
        base = {
            "id": call_id,
            "job_id": request.job_id,
            "call_kind": "EXTRACTION",
            "task_type": request.task_type,
            "logical_request_id": request.request_id,
            "parent_call_id": request.parent_call_id,
            "attempt_number": attempt_number,
            "provider_id": self.provider.provider_id,
            "model_version": getattr(self.provider, "model_version", None),
            "started_at": started_at,
            "input_sha256": hashlib.sha256(request_bytes).hexdigest(),
            "input_byte_count": len(request_bytes),
        }
        self._audit({**base, "status": "STARTED"})
        completed = Event()
        responses: list[ExtractionResponse] = []
        errors: list[Exception] = []

        def invoke() -> None:
            try:
                with self._slots:
                    responses.append(self.provider.extract(request))
            except Exception as exc:  # provider boundary must return to the caller
                errors.append(exc)
            finally:
                completed.set()

        Thread(
            target=invoke,
            name=f"qra-extraction-{request.request_id[:40]}",
            daemon=True,
        ).start()
        if not completed.wait(timeout=max(0.01, float(request.timeout_seconds))):
            error = ProviderCallError(
                "模型调用超时",
                code="EXTRACT.PROVIDER_TIMEOUT",
                retryable=True,
            )
            self._audit(
                {
                    **base,
                    "status": "FAILED",
                    "finished_at": self._now(),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                    "retryable": True,
                    "error_code": error.code,
                    "sanitized_error_message": sanitized_error_message(error),
                }
            )
            raise error
        if errors:
            error = errors[0]
            code = (
                error.code
                if isinstance(error, ProviderCallError)
                else "EXTRACT.PROVIDER_UNEXPECTED_ERROR"
            )
            self._audit(
                {
                    **base,
                    "status": (
                        "SKIPPED" if code == "EXTRACT.REQUEST_TOO_LARGE" else "FAILED"
                    ),
                    "finished_at": self._now(),
                    "elapsed_ms": round((time.perf_counter() - started) * 1000),
                    "retryable": bool(getattr(error, "retryable", False)),
                    "error_code": code,
                    "sanitized_error_message": sanitized_error_message(error),
                }
            )
            raise errors[0]
        response = responses[0].finalized()
        self._audit(
            {
                **base,
                "status": "COMPLETED",
                "finished_at": self._now(),
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "provider_id": response.provider_id,
                "model_version": response.model_version,
                "provider_request_id": response.provider_request_id,
                "raw_response_sha256": response.raw_response_sha256,
                "retryable": False,
                "usage": response.usage,
            }
        )
        return response

    def call(self, request: ExtractionRequest) -> tuple[ExtractionResponse, int]:
        retry_count = 0
        while True:
            try:
                response = self._extract_with_timeout(
                    request, attempt_number=retry_count + 1
                ).finalized()
                size = len(
                    json.dumps(response.structured_output, ensure_ascii=False).encode("utf-8")
                )
                if size > self.max_response_bytes:
                    raise ProviderCallError(
                        "模型结构化响应超过大小上限",
                        code="EXTRACT.RESPONSE_TOO_LARGE",
                        retryable=False,
                    )
                return response, retry_count
            except ProviderCallError as exc:
                if not exc.retryable or retry_count >= self.max_retries:
                    raise
                retry_count += 1
                if self.retry_delay_seconds:
                    time.sleep(self.retry_delay_seconds)


__all__ = [
    "ExtractionProvider",
    "ExtractionRequest",
    "ExtractionResponse",
    "ProviderCallError",
    "ProviderExecutor",
]

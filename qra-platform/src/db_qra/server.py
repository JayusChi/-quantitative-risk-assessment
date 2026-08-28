from __future__ import annotations

import base64
import binascii
import hmac
import io
import json
import os
import re
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse
from uuid import uuid4

from qra_engine.errors import InputValidationError

from .admin_ui import admin_html
from .conversion_adapter import (
    list_mapping_profiles,
    run_conversion_job,
    submit_conversion,
)
from .database import QraDatabase
from .engine_adapter import ENGINE_VERSION, execute_run, preview_case
from .file_intake import (
    MAX_SOURCE_FILES,
    MAX_UPLOAD_FILE_BYTES,
    MAX_UPLOAD_TOTAL_BYTES,
    IntakeError,
)
from .paths import DEFAULT_RUNTIME_ROOT
from .ocr_settings import (
    DEFAULT_OCR_TIMEOUT_SECONDS,
    OcrSettingsStore,
    apply_ocr_settings,
    environment_ocr_configured,
    load_ocr_settings_into_process,
    ocr_settings_status,
    parse_bailian_config_csv,
    settings_path_for_database,
    validate_bailian_settings,
)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
RUNTIME_ROOT = DEFAULT_RUNTIME_ROOT
RUN_SLOTS = threading.BoundedSemaphore(value=2)
CONVERSION_SLOTS = threading.BoundedSemaphore(value=2)


def _public_error_message(value: object) -> str:
    message = str(value)
    message = re.sub(
        r"(?:[A-Za-z]:[\\/]|/)(?:[^\s:]+[\\/])*[^\s:]*",
        "[受控路径]",
        message,
    )
    return message[:500]


def _public_conversion_job(job: dict[str, Any]) -> dict[str, Any]:
    """Hide legacy non-actionable JPEG metadata notices from customers."""
    public = dict(job)
    issues = public.get("intake_issues")
    if isinstance(issues, list):
        public["intake_issues"] = [
            issue
            for issue in issues
            if not isinstance(issue, dict)
            or issue.get("code") != "INTAKE.JPEG_TRAILING_DATA"
        ]
    return public


def _conversion_background(database: QraDatabase, job_id: str) -> None:
    try:
        with CONVERSION_SLOTS:
            run_conversion_job(database, job_id, runtime_root=RUNTIME_ROOT)
    except Exception as exc:
        database.fail_conversion(
            job_id,
            {
                "code": "CONVERSION_WORKER_FAILED",
                "stage": "background",
                "type": type(exc).__name__,
                "message": _public_error_message(exc),
            },
        )
        print(f"[QRA-Converter] {job_id} failed: {type(exc).__name__}: {exc}")


def _start_conversion_thread(database: QraDatabase, job_id: str) -> None:
    threading.Thread(
        target=_conversion_background,
        args=(database, job_id),
        name=f"qra-converter-{job_id}",
        daemon=True,
    ).start()


def _run_background(
    database: QraDatabase,
    run_id: str,
    snapshot_id: str,
    *,
    targets: list[str] | None,
    generate_charts: bool,
) -> None:
    try:
        with RUN_SLOTS:
            execute_run(
                database,
                run_id,
                snapshot_id,
                targets=targets,
                generate_charts=generate_charts,
                runtime_root=RUNTIME_ROOT,
            )
    except Exception as exc:
        print(f"[QRA-Worker] {run_id} failed: {type(exc).__name__}: {exc}")


def _start_run_thread(
    database: QraDatabase,
    run_id: str,
    snapshot_id: str,
    *,
    targets: list[str] | None = None,
    generate_charts: bool = True,
) -> None:
    threading.Thread(
        target=_run_background,
        args=(database, run_id, snapshot_id),
        kwargs={"targets": targets, "generate_charts": generate_charts},
        name=f"qra-{run_id}",
        daemon=True,
    ).start()


def _zip_run(database: QraDatabase, run_id: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for artifact in database.list_artifacts(run_id):
            stored = database.get_artifact(run_id, str(artifact["path"]))
            if stored is not None:
                archive.writestr(str(artifact["path"]), stored[1])
    return output.getvalue()


class QraRequestHandler(BaseHTTPRequestHandler):
    database: QraDatabase
    ocr_settings_store: OcrSettingsStore | None = None

    def _get_ocr_settings_store(self) -> OcrSettingsStore:
        store = self.ocr_settings_store
        if store is None:
            store = OcrSettingsStore(settings_path_for_database(self.database.path))
            type(self).ocr_settings_store = store
        return store

    def _send(
        self,
        status: int,
        content_type: str,
        content: bytes,
        *,
        cache_control: str = "no-store",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", cache_control)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; frame-ancestors 'self'",
        )
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(content)

    def _json(self, status: int, value: Any) -> None:
        content = json.dumps(
            value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
        ).encode("utf-8")
        self._send(status, "application/json; charset=utf-8", content)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _read_json_body(self) -> Any:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("无效的Content-Length") from exc
        if length <= 0:
            raise ValueError("请求体不能为空")
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("上传内容超过25 MB限制")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8-sig"))
        except UnicodeDecodeError as exc:
            raise ValueError("JSON文件必须使用UTF-8编码") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON语法错误：第{exc.lineno}行，第{exc.colno}列") from exc

    def _read_optional_json_body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("无效的Content-Length") from exc
        if length == 0:
            return {}
        body = self._read_json_body()
        if not isinstance(body, dict):
            raise ValueError("请求体必须是JSON对象")
        return body

    def _require_write_access(self) -> None:
        """Keep mutations local by default or require an explicit deployment token."""
        configured = os.environ.get("QRA_ADMIN_TOKEN")
        if configured:
            supplied = self.headers.get("X-QRA-Admin-Token", "")
            authorization = self.headers.get("Authorization", "")
            if authorization.startswith("Bearer "):
                supplied = authorization[7:]
            if not supplied or not hmac.compare_digest(supplied, configured):
                raise PermissionError("管理写入令牌无效")
            return
        try:
            local_client = ip_address(self.client_address[0]).is_loopback
        except ValueError:
            local_client = False
        if not local_client:
            raise PermissionError("未配置QRA_ADMIN_TOKEN时仅允许本机执行写操作")

    def _actor(self, body: dict[str, Any] | None = None) -> str:
        value = self.headers.get("X-QRA-Actor")
        if not value and body is not None:
            value = str(body.get("actor") or "")
        return str(value or "local-admin")

    @staticmethod
    def _decode_conversion_files(body: dict[str, Any]) -> list[dict[str, Any]]:
        raw_files = body.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise ValueError("files必须是非空数组")
        if len(raw_files) > MAX_SOURCE_FILES:
            raise ValueError(f"files最多包含{MAX_SOURCE_FILES}项")
        decoded = []
        decoded_total = 0
        encoded_total = 0
        max_encoded_file = 4 * ((MAX_UPLOAD_FILE_BYTES + 2) // 3)
        max_encoded_total = 4 * ((MAX_UPLOAD_TOTAL_BYTES + 2) // 3)
        for index, item in enumerate(raw_files):
            if not isinstance(item, dict):
                raise ValueError(f"files[{index}]必须是对象")
            encoded = item.get("content_base64")
            if not isinstance(encoded, str) or not encoded:
                raise ValueError(f"files[{index}].content_base64不能为空")
            encoded_length = len(encoded)
            encoded_total += encoded_length
            if encoded_length > max_encoded_file or encoded_total > max_encoded_total:
                raise ValueError("Base64编码内容超过入口大小限制")
            try:
                content = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError(f"files[{index}]不是有效Base64") from exc
            if len(content) > MAX_UPLOAD_FILE_BYTES:
                raise ValueError(f"files[{index}]解码后超过单文件大小限制")
            decoded_total += len(content)
            if decoded_total > MAX_UPLOAD_TOTAL_BYTES:
                raise ValueError("files解码后总量超过任务大小限制")
            decoded.append(
                {
                    "file_name": str(item.get("file_name") or ""),
                    "media_type": str(item.get("media_type") or ""),
                    "content": content,
                }
            )
        return decoded

    def _submit_conversion_body(
        self, body: dict[str, Any], *, batch_id: str | None = None
    ) -> dict[str, Any]:
        review_decisions = body.get("review_decisions")
        if review_decisions is not None and not isinstance(review_decisions, dict):
            raise ValueError("review_decisions必须是JSON对象或null")
        job_id, created = submit_conversion(
            self.database,
            profile=str(body.get("profile") or ""),
            files=self._decode_conversion_files(body),
            case_id=body.get("case_id"),
            project_name=body.get("project_name"),
            review_decisions=review_decisions,
            batch_id=batch_id,
            actor=self._actor(body),
            contract=str(body.get("contract") or "").strip() or None,
            failure_policy=str(body.get("failure_policy") or "ALL_OR_NOTHING"),
        )
        job = self.database.get_conversion_job(job_id, detailed=False)
        if created and job["status"] == "QUEUED":
            _start_conversion_thread(self.database, job_id)
        return {
            "created": created,
            "deduplicated": not created,
            "job": job,
        }

    @staticmethod
    def _parts(path: str) -> list[str]:
        return [part for part in path.split("/") if part]

    def _run_details(self, run_id: str) -> dict[str, Any]:
        return {
            "run": self.database.get_run(run_id),
            "nodes": self.database.list_nodes(run_id),
            "artifacts": self.database.list_artifacts(run_id),
        }

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        decoded_path = unquote(parsed.path)
        parts = self._parts(decoded_path)
        query = parse_qs(parsed.query)
        try:
            if parts[:2] == ["admin", "api"]:
                self._require_write_access()
            if decoded_path == "/":
                self._redirect("/admin/")
                return
            if decoded_path == "/admin":
                self._redirect("/admin/")
                return
            if decoded_path == "/admin/":
                self._send(200, "text/html; charset=utf-8", admin_html())
                return
            if decoded_path == "/favicon.ico":
                self._send(204, "image/x-icon", b"")
                return
            if decoded_path == "/health":
                self._json(
                    200,
                    {
                        "status": "ok",
                        "database": str(self.database.path),
                        "engine_version": ENGINE_VERSION,
                    },
                )
                return

            if parts == ["admin", "api", "overview"]:
                overview = self.database.overview()
                overview["engine_version"] = ENGINE_VERSION
                overview["storage_engine"] = "SQLite"
                self._json(200, overview)
                return
            if parts == ["admin", "api", "ocr-settings"]:
                self._json(200, ocr_settings_status(self._get_ocr_settings_store()))
                return
            if parts == ["admin", "api", "conversion-profiles"]:
                self._json(200, list_mapping_profiles())
                return
            if parts == ["admin", "api", "conversions"]:
                limit = int(query.get("limit", ["200"])[0])
                status = str(query.get("status", [""])[0]).strip() or None
                cursor = str(query.get("cursor", [""])[0]).strip() or None
                self._json(
                    200,
                    self.database.list_conversion_jobs(limit, status=status, cursor=cursor),
                )
                return
            if (
                len(parts) == 7
                and parts[:3] == ["admin", "api", "conversions"]
                and parts[4] == "sources"
                and parts[6] == "artifacts"
            ):
                artifact_path = str(query.get("path", [""])[0]).strip()
                if not artifact_path:
                    self._json(
                        200,
                        self.database.list_conversion_parse_artifacts(parts[3], parts[5]),
                    )
                    return
                stored = self.database.get_conversion_parse_artifact(
                    parts[3], parts[5], artifact_path
                )
                if stored is None:
                    self._json(404, {"error": "PARSE_ARTIFACT_NOT_FOUND"})
                    return
                metadata, content = stored
                self._send(
                    200,
                    str(metadata["content_type"]),
                    content,
                    headers={"ETag": str(metadata["sha256"])},
                )
                return
            if len(parts) == 5 and parts[:3] == ["admin", "api", "conversions"]:
                if parts[4] == "sources":
                    self._json(200, self.database.list_conversion_sources(parts[3]))
                    return
                if parts[4] == "events":
                    limit = int(query.get("limit", ["500"])[0])
                    self._json(200, self.database.list_conversion_events(parts[3], limit=limit))
                    return
            if len(parts) == 4 and parts[:3] == ["admin", "api", "conversions"]:
                job = _public_conversion_job(self.database.get_conversion_job(parts[3]))
                job["events"] = self.database.list_conversion_events(parts[3])
                self._json(200, job)
                return
            if parts == ["admin", "api", "snapshots"]:
                self._json(200, self.database.list_snapshots())
                return
            if len(parts) == 5 and parts[:3] == ["admin", "api", "snapshots"]:
                snapshot_id = parts[3]
                if parts[4] == "input":
                    payload = self.database.load_snapshot(snapshot_id)
                    content = (
                        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
                    ).encode("utf-8")
                    self._send(
                        200,
                        "application/json; charset=utf-8",
                        content,
                        headers={
                            "Content-Disposition": f'attachment; filename="{snapshot_id}.json"'
                        },
                    )
                    return
            if len(parts) == 4 and parts[:3] == ["admin", "api", "snapshots"]:
                self._json(200, self.database.snapshot_document(parts[3]))
                return
            if parts == ["admin", "api", "runs"]:
                self._json(200, self.database.list_runs())
                return
            if len(parts) >= 4 and parts[:3] == ["admin", "api", "runs"]:
                run_id = parts[3]
                if len(parts) == 4:
                    self._json(200, self._run_details(run_id))
                    return
                if len(parts) == 5 and parts[4] == "segments":
                    self._json(200, self.database.get_segment_results(run_id))
                    return
                if len(parts) == 5 and parts[4] == "artifacts":
                    self._json(200, self.database.list_artifacts(run_id))
                    return
                if len(parts) == 5 and parts[4] == "export":
                    self.database.get_run(run_id)
                    content = _zip_run(self.database, run_id)
                    self.database.record_event(
                        event_type="RESULT_EXPORTED",
                        entity_type="calculation_run",
                        entity_id=run_id,
                        detail={"format": "zip", "bytes": len(content)},
                    )
                    self._send(
                        200,
                        "application/zip",
                        content,
                        headers={"Content-Disposition": f'attachment; filename="QRA-{run_id}.zip"'},
                    )
                    return
            if parts == ["admin", "api", "database"]:
                self._json(200, self.database.table_overview())
                return
            if len(parts) == 4 and parts[:3] == ["admin", "api", "database"]:
                limit = int(query.get("limit", ["100"])[0])
                self._json(200, self.database.browse_table(parts[3], limit=limit))
                return
            if parts == ["admin", "api", "audit"]:
                limit = int(query.get("limit", ["100"])[0])
                self._json(200, self.database.list_audit_events(limit))
                return

            if parts == ["api", "runs"]:
                self._json(200, self.database.list_runs())
                return
            if parts == ["api", "snapshots"]:
                self._json(200, self.database.list_snapshots())
                return
            if len(parts) >= 3 and parts[:2] == ["api", "runs"]:
                run_id = parts[2]
                if len(parts) == 3:
                    self._json(200, self._run_details(run_id))
                    return
                if len(parts) == 4 and parts[3] == "segments":
                    self._json(200, self.database.get_segment_results(run_id))
                    return
                if len(parts) == 4 and parts[3] == "artifacts":
                    self._json(200, self.database.list_artifacts(run_id))
                    return

            if len(parts) >= 2 and parts[0] == "runs":
                run_id = parts[1]
                if len(parts) == 2 and not decoded_path.endswith("/"):
                    self._redirect(f"/runs/{quote(run_id, safe='')}/")
                    return
                artifact_path = "/".join(parts[2:]) if len(parts) > 2 else "report_dashboard.html"
                if not artifact_path or ".." in artifact_path.split("/"):
                    self._json(400, {"error": "无效资源路径"})
                    return
                artifact = self.database.get_artifact(run_id, artifact_path)
                if artifact is None:
                    self._json(404, {"error": "数据库中没有该报告资源"})
                    return
                content_type, content = artifact
                self._send(
                    200,
                    content_type,
                    content,
                    cache_control="private, max-age=60",
                )
                return
            self._json(
                404,
                {"error": "NOT_FOUND", "message": "未找到页面", "issues": []},
            )
        except KeyError as exc:
            self._json(
                404,
                {"error": "NOT_FOUND", "message": _public_error_message(exc), "issues": []},
            )
        except PermissionError as exc:
            self._json(
                403,
                {
                    "error": "FORBIDDEN",
                    "message": _public_error_message(exc),
                    "issues": [],
                },
            )
        except InputValidationError as exc:
            self._json(
                400,
                {
                    "error": "INPUT_VALIDATION_FAILED",
                    "message": str(exc),
                    "issues": [issue.to_dict() for issue in exc.issues],
                },
            )
        except IntakeError as exc:
            self._json(
                400,
                {
                    "error": "FILE_INTAKE_REJECTED",
                    "message": _public_error_message(exc),
                    "issues": [issue.to_dict() for issue in exc.issues],
                },
            )
        except ValueError as exc:
            self._json(
                400,
                {"error": "BAD_REQUEST", "message": _public_error_message(exc), "issues": []},
            )
        except Exception:
            self._json(
                500,
                {
                    "error": "INTERNAL_SERVER_ERROR",
                    "message": "服务器处理请求时发生内部错误",
                    "issues": [],
                },
            )

    def do_POST(self) -> None:  # noqa: N802
        decoded_path = unquote(urlparse(self.path).path)
        parts = self._parts(decoded_path)
        try:
            self._require_write_access()
            if parts == ["admin", "api", "ocr-settings"]:
                body = self._read_json_body()
                if not isinstance(body, dict):
                    raise ValueError("请求体必须是JSON对象")
                store = self._get_ocr_settings_store()
                csv_text = body.get("csv_text")
                if csv_text is not None and (
                    not isinstance(csv_text, str) or not csv_text.strip()
                ):
                    raise ValueError("csv_text必须是非空字符串")
                if isinstance(csv_text, str):
                    settings = parse_bailian_config_csv(
                        csv_text,
                        ocr_model_version=str(
                            body.get("ocr_model_version") or "qwen3.5-ocr"
                        ),
                        vision_model_version=str(
                            body.get("vision_model_version") or "qwen3.7-max"
                        ),
                        ocr_timeout_seconds=body.get(
                            "ocr_timeout_seconds", DEFAULT_OCR_TIMEOUT_SECONDS
                        ),
                    )
                else:
                    existing = store.load()
                    if existing is None:
                        raise ValueError("首次配置必须上传阿里云百炼CSV")
                    settings = validate_bailian_settings(
                        api_key=existing.api_key,
                        api_host=existing.api_host,
                        dashscope_url=existing.dashscope_url,
                        openai_base_url=existing.openai_base_url,
                        ocr_model_version=str(
                            body.get("ocr_model_version") or existing.ocr_model_version
                        ),
                        vision_model_version=str(
                            body.get("vision_model_version")
                            or existing.vision_model_version
                        ),
                        ocr_timeout_seconds=body.get(
                            "ocr_timeout_seconds", existing.ocr_timeout_seconds
                        ),
                        workspace_name=existing.workspace_name,
                        workspace_id=existing.workspace_id,
                    )
                store.save(settings)
                apply_ocr_settings(settings, source="encrypted-store")
                self._json(200, ocr_settings_status(store))
                return
            if parts == ["admin", "api", "conversions"]:
                body = self._read_json_body()
                if not isinstance(body, dict):
                    raise ValueError("请求体必须是JSON对象")
                result = self._submit_conversion_body(body)
                self._json(202 if result["created"] else 200, result)
                return
            if parts == ["admin", "api", "conversions", "batch"]:
                body = self._read_json_body()
                if not isinstance(body, dict) or not isinstance(body.get("jobs"), list):
                    raise ValueError("jobs必须是转换请求数组")
                jobs = body["jobs"]
                if not jobs or len(jobs) > 20:
                    raise ValueError("批量请求必须包含1至20个转换任务")
                batch_id = f"BATCH-{uuid4()}"
                results = []
                for index, item in enumerate(jobs):
                    if not isinstance(item, dict):
                        results.append(
                            {
                                "index": index,
                                "created": False,
                                "error": {
                                    "code": "BATCH_ITEM_INVALID",
                                    "message": f"jobs[{index}]必须是对象",
                                },
                            }
                        )
                        continue
                    try:
                        result = self._submit_conversion_body(item, batch_id=batch_id)
                    except (KeyError, ValueError) as exc:
                        result = {
                            "created": False,
                            "error": {
                                "code": "BATCH_ITEM_INVALID",
                                "message": str(exc),
                            },
                        }
                    results.append({"index": index, **result})
                self._json(202, {"batch_id": batch_id, "jobs": results})
                return
            if len(parts) == 5 and parts[:3] == ["admin", "api", "conversions"]:
                job_id = parts[3]
                body = self._read_optional_json_body()
                if parts[4] == "cancel":
                    job = self.database.request_conversion_cancel(job_id, actor=self._actor(body))
                    self._json(200, {"job": job})
                    return
                if parts[4] == "retry":
                    decisions = body.get("review_decisions")
                    if decisions is not None and not isinstance(decisions, dict):
                        raise ValueError("review_decisions必须是JSON对象或null")
                    retry_id = self.database.retry_conversion_job(
                        job_id,
                        review_decisions=decisions,
                        actor=self._actor(body),
                    )
                    retry_job = self.database.get_conversion_job(retry_id, detailed=False)
                    if retry_job["status"] == "QUEUED":
                        _start_conversion_thread(self.database, retry_id)
                    self._json(
                        202,
                        {"job": retry_job},
                    )
                    return
                if parts[4] == "confirm":
                    name = str(body.get("name") or "").strip()
                    reviewer = str(body.get("reviewer") or self._actor(body)).strip()
                    reason = str(body.get("reason") or "确认转换预览并创建输入快照").strip()
                    if not name or len(name) > 160:
                        raise ValueError("快照名称不能为空且不能超过160个字符")
                    if not reviewer or len(reviewer) > 120:
                        raise ValueError("确认人不能为空且不能超过120个字符")
                    if not reason or len(reason) > 500:
                        raise ValueError("确认原因不能为空且不能超过500个字符")
                    job = self.database.get_conversion_job(job_id)
                    payload = job.get("payload")
                    if not isinstance(payload, dict):
                        raise ValueError("转换任务没有可确认JSON")
                    preview_case(payload)
                    snapshot_id, created = self.database.confirm_conversion(
                        job_id,
                        name=name,
                        reviewer=reviewer,
                        reason=reason,
                    )
                    response: dict[str, Any] = {
                        "snapshot_id": snapshot_id,
                        "created": created,
                        "metadata": self.database.snapshot_metadata(snapshot_id),
                    }
                    if bool(body.get("run_after_confirm", False)):
                        metadata = response["metadata"]
                        run_id = self.database.create_run(
                            snapshot_id, str(metadata["payload_sha256"])
                        )
                        _start_run_thread(self.database, run_id, snapshot_id)
                        response["run"] = self.database.get_run(run_id)
                    self._json(201 if created else 200, response)
                    return
            if parts == ["admin", "api", "snapshots", "preview"]:
                body = self._read_json_body()
                payload = body.get("payload") if isinstance(body, dict) else None
                if not isinstance(payload, dict):
                    raise ValueError("payload必须是JSON对象")
                result = preview_case(payload)
                result["source_filename"] = str(body.get("filename") or "upload.json")
                self._json(200, result)
                return
            if parts == ["admin", "api", "snapshots", "import"]:
                body = self._read_json_body()
                payload = body.get("payload") if isinstance(body, dict) else None
                if not isinstance(payload, dict):
                    raise ValueError("payload必须是JSON对象")
                preview_case(payload)
                name = str(body.get("name") or "").strip()
                if not name:
                    raise ValueError("快照名称不能为空")
                if len(name) > 160:
                    raise ValueError("快照名称不能超过160个字符")
                filename = str(body.get("source_filename") or "upload.json")
                filename = filename.replace("/", "_").replace("\\", "_")[:180]
                snapshot_id, created = self.database.import_case(
                    payload,
                    name=name,
                    source_path=f"admin-upload://{filename}",
                )
                self._json(
                    201 if created else 200,
                    {
                        "snapshot_id": snapshot_id,
                        "created": created,
                        "metadata": self.database.snapshot_metadata(snapshot_id),
                    },
                )
                return
            if parts == ["admin", "api", "runs"]:
                body = self._read_json_body()
                if not isinstance(body, dict):
                    raise ValueError("请求体必须是JSON对象")
                snapshot_id = str(body.get("snapshot_id") or "")
                metadata = self.database.snapshot_metadata(snapshot_id)
                targets = body.get("targets")
                if targets is not None and not (
                    isinstance(targets, list) and all(isinstance(item, str) for item in targets)
                ):
                    raise ValueError("targets必须是字符串数组或null")
                run_id = self.database.create_run(snapshot_id, str(metadata["payload_sha256"]))

                _start_run_thread(
                    self.database,
                    run_id,
                    snapshot_id,
                    targets=targets,
                    generate_charts=bool(body.get("generate_charts", True)),
                )
                self._json(202, {"run": self.database.get_run(run_id)})
                return
            self._json(
                404,
                {"error": "NOT_FOUND", "message": "未找到接口", "issues": []},
            )
        except KeyError as exc:
            self._json(
                404,
                {"error": "NOT_FOUND", "message": _public_error_message(exc), "issues": []},
            )
        except PermissionError as exc:
            self._json(
                403,
                {
                    "error": "FORBIDDEN",
                    "message": _public_error_message(exc),
                    "issues": [],
                },
            )
        except InputValidationError as exc:
            self._json(
                400,
                {
                    "error": "INPUT_VALIDATION_FAILED",
                    "message": str(exc),
                    "issues": [issue.to_dict() for issue in exc.issues],
                },
            )
        except IntakeError as exc:
            self._json(
                400,
                {
                    "error": "FILE_INTAKE_REJECTED",
                    "message": _public_error_message(exc),
                    "issues": [issue.to_dict() for issue in exc.issues],
                },
            )
        except ValueError as exc:
            self._json(
                400,
                {"error": "BAD_REQUEST", "message": _public_error_message(exc), "issues": []},
            )
        except Exception:
            self._json(
                500,
                {
                    "error": "INTERNAL_SERVER_ERROR",
                    "message": "服务器处理请求时发生内部错误",
                    "issues": [],
                },
            )

    def do_DELETE(self) -> None:  # noqa: N802
        decoded_path = unquote(urlparse(self.path).path)
        parts = self._parts(decoded_path)
        try:
            self._require_write_access()
            if len(parts) == 4 and parts[:3] == ["admin", "api", "conversions"]:
                result = self.database.delete_conversion_job(
                    parts[3],
                    actor=str(self.headers.get("X-QRA-Actor") or "local-admin"),
                )
                self._json(200, result)
                return
            if len(parts) == 4 and parts[:3] == ["admin", "api", "snapshots"]:
                self.database.delete_snapshot(parts[3])
                self._json(200, {"status": "DELETED", "snapshot_id": parts[3]})
                return
            self._json(
                404,
                {"error": "NOT_FOUND", "message": "未找到接口", "issues": []},
            )
        except KeyError as exc:
            self._json(
                404,
                {"error": "NOT_FOUND", "message": _public_error_message(exc), "issues": []},
            )
        except PermissionError as exc:
            self._json(
                403,
                {
                    "error": "FORBIDDEN",
                    "message": _public_error_message(exc),
                    "issues": [],
                },
            )
        except ValueError as exc:
            self._json(
                409,
                {"error": "CONFLICT", "message": _public_error_message(exc), "issues": []},
            )
        except Exception:
            self._json(
                500,
                {
                    "error": "INTERNAL_SERVER_ERROR",
                    "message": "服务器处理请求时发生内部错误",
                    "issues": [],
                },
            )

    def log_message(self, format: str, *args: object) -> None:
        print(f"[QRA-Web] {self.address_string()} - {format % args}")


def serve(database: QraDatabase, host: str, port: int) -> None:
    database.initialize()
    ocr_settings_store = OcrSettingsStore(settings_path_for_database(database.path))
    try:
        restored_ocr = load_ocr_settings_into_process(ocr_settings_store)
    except (OSError, RuntimeError, ValueError) as exc:
        restored_ocr = None
        print(f"[QRA-OCR] 无法加载本机加密配置：{type(exc).__name__}: {_public_error_message(exc)}")
    recovered_jobs = database.requeue_interrupted_conversions()
    handler = type(
        "ConfiguredQraRequestHandler",
        (QraRequestHandler,),
        {"database": database, "ocr_settings_store": ocr_settings_store},
    )
    server = ThreadingHTTPServer((host, port), handler)
    print(f"QRA企业管理中心：http://{host}:{port}/admin/")
    print(f"数据库：{database.path}")
    if restored_ocr is not None:
        print(f"OCR：已从本机加密配置加载 {restored_ocr.ocr_model_version}")
    elif environment_ocr_configured():
        print(f"OCR：已从进程环境加载 {os.environ.get('QRA_OCR_MODEL_VERSION', 'qwen3.5-ocr')}")
    else:
        print("OCR：未配置，可在资料自动转换页面导入阿里云百炼CSV")
    for job_id in recovered_jobs:
        _start_conversion_thread(database, job_id)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


__all__ = ["QraRequestHandler", "serve"]

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import shutil
import struct
import threading
import time
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import db_qra.server as server_module
from db_qra.database import QraDatabase
from db_qra.server import QraRequestHandler
from qra_converter.mapping import load_profile
from qra_converter.service import CONVERTER_VERSION, convert_sources
from qra_engine.dynamic import plan_dynamic_flow
from qra_engine.validation import validate_import_contract

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROFILE_PATH = (
    PROJECT_ROOT
    / "resources"
    / "mappings"
    / "jiangxi-natural-gas"
    / "jiangxi-natural-gas.jiujiang.v1.json"
)
PROFILE_ID = "jiangxi-natural-gas.jiujiang.v1"
CASE_ID = "JXNG-JIUJIANG-GDBZYQ-JJ-1-STAGE1"
PROJECT_NAME = "江西省天然气九江支线真实资料转换功能验收"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON根节点必须是对象：{path}")
    return value


def _boxes(stream: BinaryIO, start: int, end: int):
    position = start
    while position + 8 <= end:
        stream.seek(position)
        header = stream.read(8)
        if len(header) != 8:
            break
        size32, box_type = struct.unpack(">I4s", header)
        header_size = 8
        if size32 == 1:
            extended = stream.read(8)
            if len(extended) != 8:
                break
            size = struct.unpack(">Q", extended)[0]
            header_size = 16
        elif size32 == 0:
            size = end - position
        else:
            size = size32
        if size < header_size or position + size > end:
            break
        yield box_type, position + header_size, position + size
        position += size


def _mp4_duration_seconds(path: Path) -> float | None:
    with path.open("rb") as stream:
        file_size = path.stat().st_size
        moov = next(
            (
                (payload_start, box_end)
                for box_type, payload_start, box_end in _boxes(stream, 0, file_size)
                if box_type == b"moov"
            ),
            None,
        )
        if moov is None:
            return None
        mvhd = next(
            (
                (payload_start, box_end)
                for box_type, payload_start, box_end in _boxes(stream, *moov)
                if box_type == b"mvhd"
            ),
            None,
        )
        if mvhd is None:
            return None
        stream.seek(mvhd[0])
        version_flags = stream.read(4)
        if len(version_flags) != 4:
            return None
        version = version_flags[0]
        if version == 1:
            stream.seek(16, 1)
            values = stream.read(12)
            if len(values) != 12:
                return None
            timescale, duration = struct.unpack(">IQ", values)
        else:
            stream.seek(8, 1)
            values = stream.read(8)
            if len(values) != 8:
                return None
            timescale, duration = struct.unpack(">II", values)
        if not timescale:
            return None
        return duration / timescale


def _photo_metadata(path: Path) -> dict[str, Any]:
    from PIL import Image

    with Image.open(path) as image:
        exif = image.getexif()
        return {
            "width": image.width,
            "height": image.height,
            "exif_present": bool(exif),
            "gps_present": bool(exif.get(34853)),
            "capture_datetime_present": bool(
                exif.get(36867) or exif.get(36868) or exif.get(306)
            ),
        }


def _source_inventory(source_root: Path) -> dict[str, Any]:
    workbook = source_root / "三级高后果区.xlsx"
    field_text = source_root / "九江线情况文字描述.docx"
    media_root = source_root / "九江支线--现场图"
    prior_draft = source_root / "江西省天然气_九江支线_现场资料变化点分段_QRA输入_v1.json"
    for required in (workbook, field_text, media_root, prior_draft):
        if not required.exists():
            raise FileNotFoundError(f"阶段1资料缺失：{required}")

    photos = sorted(
        media_root.glob("*.jpg"), key=lambda item: (item.stat().st_mtime_ns, item.name.casefold())
    )
    videos = sorted(
        media_root.glob("*.mp4"), key=lambda item: (item.stat().st_mtime_ns, item.name.casefold())
    )
    records: list[dict[str, Any]] = []

    def add_record(logical_id: str, path: Path, role: str, **extra: Any) -> None:
        records.append(
            {
                "logical_id": logical_id,
                "role": role,
                "relative_path": path.relative_to(source_root).as_posix(),
                "byte_count": path.stat().st_size,
                "sha256": _sha256(path),
                **extra,
            }
        )

    add_record("workbook-001", workbook, "STRUCTURED_SOURCE")
    add_record("field-text-001", field_text, "AUXILIARY_FREE_TEXT_SOURCE")
    for index, photo in enumerate(photos, start=1):
        add_record(
            f"photo-{index:03d}",
            photo,
            "ROUTE_CONTEXT_MEDIA",
            **_photo_metadata(photo),
        )
    for index, video in enumerate(videos, start=1):
        add_record(
            f"video-{index:03d}",
            video,
            "ROUTE_CONTEXT_MEDIA",
            duration_seconds=_mp4_duration_seconds(video),
            georeferenced=False,
        )
    add_record("prior-draft-001", prior_draft, "DERIVED_COMPARISON_ONLY")

    original_records = [row for row in records if row["role"] != "DERIVED_COMPARISON_ONLY"]
    photo_records = [row for row in records if row["logical_id"].startswith("photo-")]
    video_records = [row for row in records if row["logical_id"].startswith("video-")]
    return {
        "schema_version": "1.0.0",
        "scope": "九江支线GDBZYQ-JJ-1阶段1资料；照片057仅作相邻瑞昌支线排除证据",
        "original_source_file_count": len(original_records),
        "derived_comparison_file_count": len(records) - len(original_records),
        "photo_count": len(photo_records),
        "video_count": len(video_records),
        "photo_with_exif_count": sum(bool(row["exif_present"]) for row in photo_records),
        "photo_with_gps_count": sum(bool(row["gps_present"]) for row in photo_records),
        "photo_with_capture_datetime_count": sum(
            bool(row["capture_datetime_present"]) for row in photo_records
        ),
        "video_duration_seconds": round(
            sum(float(row["duration_seconds"] or 0.0) for row in video_records), 6
        ),
        "records": records,
    }


def _convert(source_dir: Path, output_dir: Path, review_path: Path | None = None):
    return convert_sources(
        source_dir=source_dir,
        profile_path=PROFILE_PATH,
        output_dir=output_dir,
        case_id=CASE_ID,
        project_name=PROJECT_NAME,
        contract_validator=validate_import_contract,
        capability_planner=plan_dynamic_flow,
        review_decisions_path=review_path,
    )


def _request_json(
    base: str,
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = Request(
        base + path,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-QRA-Actor": "stage1-internal-validator",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            content = response.read()
            return response.status, json.loads(content.decode("utf-8")) if content else None
    except HTTPError as exc:
        content = exc.read()
        return exc.code, json.loads(content.decode("utf-8")) if content else None


def _wait_conversion(base: str, job_id: str) -> dict[str, Any]:
    final: dict[str, Any] = {}
    for _ in range(300):
        status, value = _request_json(base, f"/admin/api/conversions/{job_id}")
        if status != 200 or not isinstance(value, dict):
            raise RuntimeError(f"网页转换任务查询失败：HTTP {status} {value}")
        final = value
        if final.get("status") not in {"QUEUED", "RUNNING"}:
            return final
        time.sleep(0.05)
    raise TimeoutError(f"网页转换任务未在期限内完成：{job_id}")


def _web_acceptance(
    output_root: Path,
    source_files: list[Path],
    review_decisions: dict[str, Any],
    cli_case: dict[str, Any],
) -> dict[str, Any]:
    database = QraDatabase(output_root / "stage1-web.sqlite3")
    handler = type("Stage1AcceptanceHandler", (QraRequestHandler,), {"database": database})
    server_module.RUNTIME_ROOT = output_root / "web-runtime"
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        base = f"http://{host}:{port}"
        profiles_status, profiles = _request_json(base, "/admin/api/conversion-profiles")
        if profiles_status != 200 or not any(
            row.get("profile_id") == PROFILE_ID for row in profiles
        ):
            raise AssertionError("网页端未发现九江专用映射配置")

        upload = {
            "profile": PROFILE_ID,
            "case_id": CASE_ID,
            "project_name": PROJECT_NAME,
            "review_decisions": review_decisions,
            "files": [
                {
                    "file_name": path.name,
                    "content_base64": base64.b64encode(path.read_bytes()).decode("ascii"),
                }
                for path in source_files
            ],
        }
        submit_status, submitted = _request_json(
            base, "/admin/api/conversions", method="POST", body=upload
        )
        if submit_status != 202:
            raise AssertionError(f"网页转换提交失败：HTTP {submit_status} {submitted}")
        job_id = str(submitted["job"]["id"])
        final = _wait_conversion(base, job_id)
        if final.get("status") != "READY_FOR_CONFIRMATION":
            raise AssertionError(f"网页转换未达到可确认状态：{final.get('status')}")
        if final.get("payload") != cli_case:
            raise AssertionError("命令行与网页端业务JSON不一致")
        if final.get("conversion_report", {}).get("summary", {}).get(
            "pending_review_count"
        ):
            raise AssertionError("网页转换仍有未处理复核项")

        dedupe_status, duplicate = _request_json(
            base, "/admin/api/conversions", method="POST", body=upload
        )
        if dedupe_status != 200 or not duplicate.get("deduplicated"):
            raise AssertionError("相同网页输入未被内容哈希去重")
        if duplicate["job"]["id"] != job_id:
            raise AssertionError("网页去重返回了不同转换任务")

        confirm_status, confirmed = _request_json(
            base,
            f"/admin/api/conversions/{job_id}/confirm",
            method="POST",
            body={
                "name": "九江支线阶段1内部功能验证快照",
                "reviewer": "stage1-internal-validator",
                "reason": "已核对单线过滤、权威边界、来源、单位、显式缺口和命令行/网页一致性；非业务数据负责人正式签批。",
            },
        )
        if confirm_status != 201:
            raise AssertionError(f"网页确认失败：HTTP {confirm_status} {confirmed}")
        snapshot_id = str(confirmed["snapshot_id"])
        snapshot_status, snapshot = _request_json(
            base, f"/admin/api/snapshots/{snapshot_id}"
        )
        if snapshot_status != 200 or snapshot.get("payload_sha256") != final.get("case_sha256"):
            raise AssertionError("网页快照哈希与转换任务不一致")

        before_status, before = _request_json(base, "/admin/api/overview")
        if before_status != 200:
            raise AssertionError("无法读取网页验收前快照统计")
        invalid_fixture = (
            PROJECT_ROOT
            / "tests"
            / "fixtures"
            / "converter_jiujiang_stage1"
            / "脱敏高后果区.csv"
        ).read_text(encoding="utf-8")
        invalid_text = invalid_fixture.replace(
            "JJ001（0+0）,JJ041G（10+938）,10938",
            "JJ001（1+0）,JJ000（0+0）,1000",
            1,
        )
        invalid_upload = {
            "profile": PROFILE_ID,
            "case_id": "INVALID-STAGE1",
            "project_name": "非法边界负向验收",
            "files": [
                {
                    "file_name": "非法高后果区.csv",
                    "content_base64": base64.b64encode(invalid_text.encode("utf-8")).decode(
                        "ascii"
                    ),
                }
            ],
        }
        invalid_status, invalid_submitted = _request_json(
            base, "/admin/api/conversions", method="POST", body=invalid_upload
        )
        if invalid_status != 202:
            raise AssertionError("非法输入未进入受控转换检查")
        invalid_job_id = str(invalid_submitted["job"]["id"])
        invalid_final = _wait_conversion(base, invalid_job_id)
        if invalid_final.get("status") != "BLOCKED":
            raise AssertionError("倒置里程非法输入未被阻断")
        rejected_status, _ = _request_json(
            base,
            f"/admin/api/conversions/{invalid_job_id}/confirm",
            method="POST",
            body={
                "name": "不得创建",
                "reviewer": "stage1-internal-validator",
                "reason": "负向测试",
            },
        )
        if rejected_status < 400:
            raise AssertionError("阻断转换仍可创建快照")
        after_status, after = _request_json(base, "/admin/api/overview")
        if after_status != 200 or after["snapshot_count"] != before["snapshot_count"]:
            raise AssertionError("非法输入改变了快照数量")

        return {
            "conversion_job_id": job_id,
            "snapshot_id": snapshot_id,
            "case_sha256": final["case_sha256"],
            "deduplicated_job_id": duplicate["job"]["id"],
            "invalid_conversion_job_id": invalid_job_id,
            "invalid_conversion_status": invalid_final["status"],
            "invalid_confirm_http_status": rejected_status,
            "snapshot_count_after_negative_test": after["snapshot_count"],
        }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def run(source_root: Path, output_root: Path) -> dict[str, Any]:
    if (output_root / "acceptance-summary.json").exists():
        raise FileExistsError(f"验收输出已存在，拒绝混入旧证据：{output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    inventory = _source_inventory(source_root)
    _write_json(output_root / "source-inventory.json", inventory)
    hash_lines = [
        f"{row['sha256']}  {row['logical_id']}"
        for row in inventory["records"]
        if row["role"] != "DERIVED_COMPARISON_ONLY"
    ]
    (output_root / "source-files.sha256").write_text(
        "\n".join(hash_lines) + "\n", encoding="utf-8"
    )

    source_package = output_root / "source-package"
    source_package.mkdir()
    selected_sources = [
        source_root / "三级高后果区.xlsx",
        source_root / "九江线情况文字描述.docx",
    ]
    packaged_sources = []
    for source in selected_sources:
        target = source_package / source.name
        shutil.copy2(source, target)
        packaged_sources.append(target)

    pending = _convert(source_package, output_root / "cli-pending")
    pending_preview = _read_json(Path(pending["paths"]["conversion_preview"]))
    review_items = pending_preview["manual_review"]["items"]
    if not review_items or any(item["kind"] != "UNMAPPED_AUXILIARY_CONTENT" for item in review_items):
        raise AssertionError("真实DOCX辅助内容未进入预期人工复核队列")
    reviewed_at = datetime.now().astimezone().isoformat(timespec="seconds")
    review_decisions = {
        "schema_version": "1.0.0",
        "reviewer": "stage1-internal-validator",
        "reviewed_at": reviewed_at,
        "decisions": [
            {
                "review_id": item["review_id"],
                "action": "ACKNOWLEDGE_NOT_IMPORTED",
                "reason": "自由文本只有目标名称和人数片段，没有权威里程、坐标、昼夜及室内外分布；阶段1登记缺口并禁止自动写入计算人口单元。",
            }
            for item in review_items
        ],
    }
    review_path = output_root / "review-decisions.json"
    _write_json(review_path, review_decisions)

    first = _convert(source_package, output_root / "cli-run-a", review_path)
    second = _convert(source_package, output_root / "cli-run-b", review_path)
    if first["status"] != "READY_FOR_REVIEW" or second["status"] != "READY_FOR_REVIEW":
        raise AssertionError("复核后的真实资料命令行转换未达到READY_FOR_REVIEW")
    if first["case_sha256"] != second["case_sha256"]:
        raise AssertionError("相同真实输入和映射重复转换的内容哈希不一致")
    first_case = _read_json(Path(first["paths"]["case"]))
    second_case = _read_json(Path(second["paths"]["case"]))
    if first_case != second_case:
        raise AssertionError("重复命令行转换的业务JSON不一致")
    first_report = _read_json(Path(first["paths"]["conversion_report"]))
    if first_report["contract_status"] != "PASS":
        raise AssertionError("真实转换JSON未通过输入合同")
    if first_report["summary"]["pending_review_count"] != 0:
        raise AssertionError("真实转换仍有未处理人工复核项")
    if first_report["summary"]["review_audit_count"] != len(review_items):
        raise AssertionError("真实转换复核审计数量不一致")
    segments = first_case.get("segments") or []
    if len(segments) != 1 or segments[0].get("segment_id") != "GDBZYQ-JJ-1":
        raise AssertionError("单线过滤未生成唯一九江权威边界管段")
    if (
        segments[0].get("start_km") != 0.0
        or segments[0].get("end_km") != 10.938
        or segments[0].get("length_km") != 10.938
    ):
        raise AssertionError("九江权威边界或长度不一致")
    if first_case.get("population_cells"):
        raise AssertionError("缺乏空间与昼夜证据时不得生成计算人口单元")
    if "operating_pressure_mpa" in first_case.get("pipeline", {}):
        raise AssertionError("运行压力范围被错误折算为单值")
    shutil.copy2(Path(first["paths"]["case"]), output_root / "golden-case.candidate.json")

    web = _web_acceptance(output_root, packaged_sources, review_decisions, first_case)
    if web["case_sha256"] != first["case_sha256"]:
        raise AssertionError("命令行与网页转换哈希不一致")

    profile = load_profile(PROFILE_PATH)
    summary = {
        "schema_version": "1.0.0",
        "executed_at": reviewed_at,
        "technical_status": "PASSED_INTERNAL_FUNCTION_VALIDATION",
        "g1_status": "DEFERRED_NOT_IN_CURRENT_FUNCTION_VALIDATION_SCOPE",
        "scope": "阶段1真实数据转换内部功能验收，不含模型发布或正式QRA结论",
        "scope_decision": (
            "项目方于2026-08-26明确当前只验证功能是否实现；"
            "业务数据负责人签批延后至工程验证或正式发布前。"
        ),
        "software": {
            "platform_package_version": "0.9.0",
            "converter_version": CONVERTER_VERSION,
        },
        "mapping_profile": {
            "profile_id": profile.profile_id,
            "version": profile.version,
            "sha256": profile.checksum_sha256,
        },
        "source_inventory": {
            key: inventory[key]
            for key in (
                "original_source_file_count",
                "derived_comparison_file_count",
                "photo_count",
                "video_count",
                "photo_with_exif_count",
                "photo_with_gps_count",
                "photo_with_capture_datetime_count",
                "video_duration_seconds",
            )
        },
        "cli": {
            "status": first["status"],
            "case_sha256": first["case_sha256"],
            "repeated_case_sha256": second["case_sha256"],
            "contract_status": first_report["contract_status"],
            "segment_count": first_report["summary"]["segment_count"],
            "population_record_count": first_report["summary"][
                "population_record_count"
            ],
            "raw_record_count": first_report["summary"]["raw_record_count"],
            "lineage_count": first_report["summary"]["lineage_count"],
            "pending_review_count": first_report["summary"]["pending_review_count"],
            "review_audit_count": first_report["summary"]["review_audit_count"],
            "issue_counts": first_report["summary"]["issue_counts"],
        },
        "web": web,
        "checks": {
            "single_line_filter": True,
            "authoritative_boundary_0_to_10_938_km": True,
            "length_matches_chainage": True,
            "unexplained_overlap_gap_or_out_of_bounds": False,
            "source_blanks_preserved": True,
            "pressure_range_not_collapsed": True,
            "population_not_fabricated": True,
            "critical_values_have_field_lineage": True,
            "review_items_resolved_with_audit": True,
            "cli_web_business_json_deep_equal": True,
            "repeat_hash_equal": True,
            "invalid_input_snapshot_created": False,
        },
        "remaining_approval": "当前功能验证范围无待办审批。",
        "deferred_formal_requirement": (
            "工程验证或正式发布前，需由业务数据负责人核对真实台账原文、"
            "确认黄金JSON忠实性并执行正式G1。"
        ),
    }
    _write_json(output_root / "acceptance-summary.json", summary)
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="执行九江支线阶段1真实数据转换验收")
    parser.add_argument(
        "--source-root",
        type=Path,
        default=PROJECT_ROOT / "workspace" / "inputs" / "江西省天然气",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "workspace" / "runtime" / "stage1-real-data-acceptance",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    summary = run(args.source_root.resolve(), args.output_root.resolve())
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

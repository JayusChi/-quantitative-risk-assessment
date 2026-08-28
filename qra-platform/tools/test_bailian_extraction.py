"""Run a secret-safe synthetic stage-four smoke test against Qwen."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
CONTRACT_ROOT = PROJECT_ROOT / "resources" / "contracts" / "part1" / "v1"

if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from db_qra.ocr_settings import parse_bailian_config_csv  # noqa: E402
from qra_converter.contract_catalog import load_contract_catalog  # noqa: E402
from qra_converter.contracts import SourceReference  # noqa: E402
from qra_converter.extraction.aliyun_bailian import (  # noqa: E402
    AliyunBailianExtractionProvider,
)
from qra_converter.extraction.ports import ExtractionRequest, ExtractionResponse  # noqa: E402
from qra_converter.orchestration.workflow import Stage4Workflow  # noqa: E402
from qra_converter.parsing.contracts import (  # noqa: E402
    ParsedDocument,
    TextBlock,
    source_fragment_sha256,
)


def run_smoke(config_csv: Path, *, model_version: str | None = None) -> dict[str, object]:
    settings = parse_bailian_config_csv(config_csv.read_text(encoding="utf-8-sig"))
    provider = AliyunBailianExtractionProvider(
        openai_base_url=settings.openai_base_url,
        api_key=settings.api_key,
        model_version=model_version or settings.extraction_model_version,
        default_timeout_seconds=settings.extraction_timeout_seconds,
    )

    class CapturingProvider:
        provider_id = provider.provider_id
        deployment_scope = provider.deployment_scope
        model_version = provider.model_version
        default_timeout_seconds = provider.default_timeout_seconds
        max_retries = provider.max_retries
        max_concurrency = provider.max_concurrency

        def __init__(self) -> None:
            self.responses: list[tuple[str, dict[str, object]]] = []

        def extract(self, request: ExtractionRequest) -> ExtractionResponse:
            response = provider.extract(request)
            self.responses.append((request.task_type, response.structured_output))
            return response

    capturing_provider = CapturingProvider()
    text = "A线运行压力为5 MPa，数据日期为2026年8月。"
    block = TextBlock(
        block_id="BLOCK-REAL-MODEL-SMOKE",
        text=text,
        normalized_text=text,
        reading_order=0,
        block_type="PARAGRAPH",
        extraction_method="NATIVE_TEXT",
        source_fragment_sha256=source_fragment_sha256(text),
    )
    document = ParsedDocument(
        document_id="DOC-REAL-MODEL-SMOKE",
        source=SourceReference(
            "SRC-REAL-MODEL-SMOKE",
            "synthetic-smoke.docx",
            "synthetic",
            source_fragment_sha256(text),
        ),
        media_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
        document_kind="DOCX",
        parser_id="synthetic-smoke",
        parser_version="1.0.0",
        page_count=1,
        text_blocks=(block,),
    ).finalized()
    result = Stage4Workflow(
        catalog=load_contract_catalog(CONTRACT_ROOT),
        provider=capturing_provider,
    ).run(
        job_id="STAGE4-REAL-MODEL-SMOKE",
        documents=(document,),
        mapping_version="synthetic-smoke/1.0.0",
        field_subset=("pipeline.operating_pressure_mpa",),
        external_sharing_allowed=True,
    )
    calls = [
        call
        for step in result.steps
        for call in step.output.get("model_calls", [])
        if isinstance(call, dict)
    ]
    failed_calls = [call for call in calls if call.get("status") != "COMPLETED"]
    model_candidates = [
        candidate
        for candidate in result.candidates
        if candidate.get("extraction_method") == "MODEL_EXTRACTION"
    ]
    provider_issues = [
        issue
        for issue in result.issues
        if str(issue.get("code") or "").startswith("EXTRACT.PROVIDER_")
    ]
    field_items = [
        item
        for task_type, output in capturing_provider.responses
        if task_type == "EXTRACT_FIELDS"
        for item in output.get("items", [])
        if isinstance(item, dict)
    ]
    passed = bool(calls) and not failed_calls and not provider_issues and bool(model_candidates)
    return {
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if passed else "FAIL",
        "provider_id": provider.provider_id,
        "model_version": provider.model_version,
        "model_call_count": len(calls),
        "failed_model_call_count": len(failed_calls),
        "failure_codes": sorted(
            {
                str(call.get("error_code") or "UNKNOWN")
                for call in failed_calls
            }
        ),
        "model_candidate_count": len(model_candidates),
        "field_response_item_count": len(field_items),
        "field_not_found_count": sum(item.get("not_found") is True for item in field_items),
        "field_response_keys": sorted({str(key) for item in field_items for key in item}),
        "classification_count": len(result.classifications),
        "entity_count": len(result.entities),
        "issue_codes": sorted({str(issue.get("code") or "") for issue in result.issues}),
        "provider_issue_codes": sorted(
            {str(issue.get("code") or "UNKNOWN") for issue in provider_issues}
        ),
        "provider_issue_messages": sorted(
            {str(issue.get("message") or "")[:300] for issue in provider_issues}
        ),
        "evidence_binding_rate": result.metrics.get("evidence_binding_rate"),
        "result_sha256": result.sha256,
        "synthetic_input_only": True,
        "secret_or_endpoint_recorded": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="使用桌面百炼CSV执行不输出密钥或正文的第四阶段真实模型冒烟"
    )
    parser.add_argument("--config-csv", type=Path, required=True)
    parser.add_argument("--model", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--record", type=Path)
    arguments = parser.parse_args()
    try:
        record = run_smoke(arguments.config_csv, model_version=arguments.model)
    except Exception as exc:  # public CLI boundary; provider already redacts secrets
        record = {
            "status": "FAIL",
            "error_type": type(exc).__name__,
            "message": str(exc)[:500],
        }
    if arguments.record:
        target = arguments.record.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
    if arguments.json:
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            f"千问信息提取真实冒烟：{record['status']}；"
            f"模型={record.get('model_version', 'unknown')}；"
            f"调用={record.get('model_call_count', 0)}"
        )
    return 0 if record["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

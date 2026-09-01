from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError

from PIL import Image

from qra_converter.image_processing.ocr_planner import (
    merge_ocr_unit_responses,
    plan_ocr_image_units,
)
from qra_converter.image_processing.preprocess import preprocess_image
from qra_converter.ocr.aliyun_bailian import AliyunBailianOcrProvider
from qra_converter.ocr.payload_policy import OcrPayloadPolicy
from qra_converter.ocr.ports import (
    OcrRequest,
    OcrRequestTooLarge,
    OcrResponse,
    OcrTextBlock,
)
from qra_converter.ocr.service import OcrService
from qra_converter.parsing.contracts import BoundingBox


class _NoNetwork:
    def __call__(self, *_args, **_kwargs):
        raise AssertionError("oversized request must not be sent")


class _AuditedProvider:
    provider_id = "audit-fixture"
    model_version = "audit-v1"

    def __init__(self, *, reject_locally: bool = False) -> None:
        self.reject_locally = reject_locally

    def build_request_bytes(self, request: OcrRequest) -> bytes:
        if self.reject_locally:
            raise OcrRequestTooLarge("data:image/png;base64,SECRET")
        payload = {"media_sha256": hashlib.sha256(request.image_bytes).hexdigest()}
        return json.dumps(payload).encode()

    def recognize(self, _request: OcrRequest) -> OcrResponse:
        return OcrResponse(
            self.provider_id,
            self.model_version,
            text_blocks=(
                OcrTextBlock("SENSITIVE_DOCUMENT_BODY", BoundingBox(1, 2, 3, 4), 0.9),
            ),
            raw_response_sha256=hashlib.sha256(b"response").hexdigest(),
        )


class RoadmapStage3OcrTests(unittest.TestCase):
    @staticmethod
    def request(content: bytes = b"image") -> OcrRequest:
        return OcrRequest(
            content,
            100,
            100,
            ("zh-Hans",),
            False,
            "REQ-P0",
            1.0,
            image_content_type="image/jpeg",
        )

    def test_final_serialized_body_boundary_is_enforced(self) -> None:
        baseline = AliyunBailianOcrProvider(
            dashscope_url="https://dashscope.aliyuncs.com/api/v1",
            api_key="sk-test-redacted",
        )
        request = self.request(b"x" * 10_000)
        data_uri_bytes = len(baseline.data_uri_bytes(request))
        body_bytes = len(baseline.build_request_bytes(request))
        policy = OcrPayloadPolicy(
            max_data_uri_bytes=data_uri_bytes,
            max_http_body_bytes=body_bytes - 1,
        )
        provider = AliyunBailianOcrProvider(
            dashscope_url="https://dashscope.aliyuncs.com/api/v1",
            api_key="sk-test-redacted",
            payload_policy=policy,
            opener=_NoNetwork(),
        )
        with self.assertRaises(OcrRequestTooLarge):
            provider.recognize(request)
        self.assertEqual(len(provider.build_request_bytes(request)), body_bytes)

    def test_large_noisy_image_is_encoded_or_tiled_under_actual_budget(self) -> None:
        image = Image.effect_noise((2800, 2800), 80).convert("L")
        raw = io.BytesIO()
        image.save(raw, format="JPEG", quality=98)
        processed = preprocess_image(raw.getvalue())
        policy = OcrPayloadPolicy(
            max_data_uri_bytes=700_000,
            max_http_body_bytes=750_000,
            model_max_pixels=4_000_000,
            maximum_tiles_per_page=64,
        )

        def serialized(content: bytes, content_type: str, *_args) -> int:
            prefix = len(f"data:{content_type};base64,".encode())
            return prefix + 4 * ((len(content) + 2) // 3) + 2_000

        plan = plan_ocr_image_units(
            processed,
            policy=policy,
            page_number=1,
            serialized_size=serialized,
        )
        self.assertTrue(plan.units)
        self.assertGreaterEqual(len(plan.units), 1)
        self.assertTrue(
            all(
                serialized(
                    unit.encoded_bytes,
                    unit.content_type,
                    unit.width,
                    unit.height,
                    unit.unit_id,
                )
                <= policy.max_http_body_bytes
                for unit in plan.units
            )
        )
        self.assertTrue(
            all(
                unit.width * unit.height <= policy.model_max_pixels
                for unit in plan.units
            )
        )

    def test_long_image_has_stable_overlap_and_reversible_coordinates(self) -> None:
        image = Image.new("L", (300, 6000), "white")
        raw = io.BytesIO()
        image.save(raw, format="PNG")
        processed = preprocess_image(raw.getvalue())
        policy = OcrPayloadPolicy(maximum_tiles_per_page=16, tile_overlap_pixels=60)
        first = plan_ocr_image_units(processed, policy=policy, page_number=2)
        second = plan_ocr_image_units(processed, policy=policy, page_number=2)
        self.assertGreater(len(first.units), 1)
        self.assertEqual(
            [unit.unit_id for unit in first.units], [unit.unit_id for unit in second.units]
        )
        unit = first.units[-1]
        point = unit.tile_to_processed_transform.point(0, 0)
        restored = unit.tile_to_processed_transform.inverse().point(*point)
        self.assertAlmostEqual(restored[0], 0, places=6)
        self.assertAlmostEqual(restored[1], 0, places=6)

    def test_overlap_deduplicates_same_text_and_preserves_conflict(self) -> None:
        image = Image.new("L", (300, 6000), "white")
        raw = io.BytesIO()
        image.save(raw, format="PNG")
        plan = plan_ocr_image_units(
            preprocess_image(raw.getvalue()),
            policy=OcrPayloadPolicy(maximum_tiles_per_page=16, tile_overlap_pixels=120),
            page_number=1,
        )
        first, second = plan.units[:2]
        first_height = first.height
        overlap_y = max(0, first_height - 100)
        responses = [
            (
                first,
                OcrResponse(
                    "fixture",
                    "1",
                    text_blocks=(
                        OcrTextBlock("相同文字", BoundingBox(10, overlap_y, 100, 80), 0.8),
                    ),
                    raw_response_sha256=hashlib.sha256(b"1").hexdigest(),
                ),
            ),
            (
                second,
                OcrResponse(
                    "fixture",
                    "1",
                    text_blocks=(
                        OcrTextBlock("相同文字", BoundingBox(10, 20, 100, 80), 0.95),
                        OcrTextBlock("冲突文字", BoundingBox(10, 20, 100, 80), 0.9),
                    ),
                    raw_response_sha256=hashlib.sha256(b"2").hexdigest(),
                ),
            ),
        ]
        merged = merge_ocr_unit_responses(responses)
        self.assertEqual(sum(block.text == "相同文字" for block in merged.text_blocks), 1)
        self.assertIn("冲突文字", {block.text for block in merged.text_blocks})
        self.assertIn("PARSE.OCR_OVERLAP_CONFLICT", merged.issues)

    def test_http_413_and_size_wording_are_adaptable_errors(self) -> None:
        def opener(*_args, **_kwargs):
            body = io.BytesIO(b'{"message":"max bytes per data-uri exceeded"}')
            raise HTTPError("https://redacted", 422, "bad", {}, body)

        provider = AliyunBailianOcrProvider(
            dashscope_url="https://dashscope.aliyuncs.com/api/v1",
            api_key="sk-test-redacted",
            opener=opener,
        )
        with self.assertRaises(OcrRequestTooLarge):
            provider.recognize(self.request())

    def test_success_cache_and_local_rejection_are_attempt_audited_without_payload(self) -> None:
        events: list[dict[str, object]] = []
        with tempfile.TemporaryDirectory() as temporary:
            service = OcrService(
                _AuditedProvider(),
                cache_dir=Path(temporary),
                audit_callback=events.append,
            )
            service.recognize(self.request())
            service.recognize(self.request())
        self.assertEqual(
            [event["status"] for event in events],
            ["STARTED", "COMPLETED", "CACHED"],
        )
        self.assertEqual(events[0]["id"], events[1]["id"])
        serialized = json.dumps(events, ensure_ascii=False)
        self.assertNotIn("data:image", serialized)
        self.assertNotIn("SENSITIVE_DOCUMENT_BODY", serialized)

        rejected: list[dict[str, object]] = []
        with self.assertRaises(OcrRequestTooLarge):
            OcrService(
                _AuditedProvider(reject_locally=True),
                max_retries=0,
                audit_callback=rejected.append,
            ).recognize(self.request())
        self.assertEqual([event["status"] for event in rejected], ["SKIPPED"])
        self.assertEqual(rejected[0]["error_code"], "PARSE.OCR_REQUEST_TOO_LARGE")
        self.assertNotIn("SECRET", str(rejected))


if __name__ == "__main__":
    unittest.main()

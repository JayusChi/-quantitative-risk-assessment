"""Deterministic extraction provider for golden tests and offline acceptance."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .ports import ExtractionRequest, ExtractionResponse, ProviderCallError


class FixtureExtractionProvider:
    provider_id = "fixture"
    deployment_scope = "LOCAL"

    def __init__(self, fixtures: dict[str, Any], *, model_version: str = "fixture-v1") -> None:
        self.fixtures = fixtures
        self.model_version = model_version
        self.calls: list[ExtractionRequest] = []
        self._indexes: dict[str, int] = defaultdict(int)

    def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        self.calls.append(request)
        fixture = self.fixtures.get(request.task_type)
        if isinstance(fixture, list):
            index = self._indexes[request.task_type]
            self._indexes[request.task_type] += 1
            if index >= len(fixture):
                raise ProviderCallError(
                    f"fixture耗尽：{request.task_type}",
                    code="EXTRACT.FIXTURE_EXHAUSTED",
                )
            fixture = fixture[index]
        if isinstance(fixture, Exception):
            raise fixture
        if fixture is None:
            fixture = {"items": []}
        if not isinstance(fixture, dict):
            raise ProviderCallError(
                "fixture输出必须是对象",
                code="EXTRACT.FIXTURE_INVALID",
            )
        return ExtractionResponse(
            provider_id=self.provider_id,
            model_id="deterministic-fixture",
            model_version=self.model_version,
            structured_output=fixture,
            usage={"fixture": True},
            provider_request_id=f"fixture:{request.request_id}",
        )


__all__ = ["FixtureExtractionProvider"]

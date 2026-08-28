"""Stable contracts for the stage-four candidate pipeline."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

STAGE4_CONTRACT_VERSION = "qra.stage4-candidates/1.0.0"


class WorkflowStatus(str, Enum):
    PARSED = "PARSED"
    CLASSIFYING = "CLASSIFYING"
    CLASSIFIED = "CLASSIFIED"
    EXTRACTING_ENTITIES = "EXTRACTING_ENTITIES"
    ENTITIES_READY = "ENTITIES_READY"
    EXTRACTING_FIELDS = "EXTRACTING_FIELDS"
    CANDIDATES_READY = "CANDIDATES_READY"
    NORMALIZING = "NORMALIZING"
    NORMALIZED = "NORMALIZED"
    FUSING = "FUSING"
    FUSION_READY = "FUSION_READY"
    QUALITY_CHECKING = "QUALITY_CHECKING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class StepResult:
    step: str
    status: str
    input_sha256: str
    output_sha256: str
    output: dict[str, Any]
    started_at: str
    finished_at: str
    retry_count: int = 0
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Stage4Result:
    job_id: str
    status: WorkflowStatus
    classifications: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    entities: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    candidates: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    evidence: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    relationships: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    fusion_groups: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    issues: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    metrics: dict[str, Any] = field(default_factory=dict)
    capability_plan: dict[str, Any] = field(default_factory=dict)
    steps: tuple[StepResult, ...] = field(default_factory=tuple)
    state_history: tuple[str, ...] = field(default_factory=tuple)
    contract_version: str = STAGE4_CONTRACT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "classifications": list(self.classifications),
            "entities": list(self.entities),
            "candidates": list(self.candidates),
            "evidence": list(self.evidence),
            "relationships": list(self.relationships),
            "fusion_groups": list(self.fusion_groups),
            "issues": list(self.issues),
            "metrics": self.metrics,
            "capability_plan": self.capability_plan,
            "steps": [step.to_dict() for step in self.steps],
            "state_history": list(self.state_history),
            "contract_version": self.contract_version,
        }

    @property
    def sha256(self) -> str:
        payload = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


__all__ = ["STAGE4_CONTRACT_VERSION", "Stage4Result", "StepResult", "WorkflowStatus"]

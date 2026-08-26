from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .errors import ModelNotReadyError


MODEL_SPEC_ROOT = Path(__file__).resolve().parent / "model_specs"
RELEASED_STATUS = "RELEASED"


@dataclass(frozen=True, slots=True)
class ModelRegistration:
    model_id: str
    domain: str
    status: str
    implementation: str
    spec_file: str | None
    release_blockers: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ModelRegistration":
        return cls(
            model_id=str(value["model_id"]),
            domain=str(value["domain"]),
            status=str(value["status"]),
            implementation=str(value["implementation"]),
            spec_file=value.get("spec_file"),
            release_blockers=tuple(str(item) for item in value.get("release_blockers", [])),
        )

    @property
    def released(self) -> bool:
        return self.status == RELEASED_STATUS

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_id": self.model_id,
            "domain": self.domain,
            "status": self.status,
            "implementation": self.implementation,
            "spec_file": self.spec_file,
            "release_blockers": list(self.release_blockers),
            "released": self.released,
        }


@lru_cache(maxsize=1)
def load_model_registry() -> tuple[ModelRegistration, ...]:
    registry_path = MODEL_SPEC_ROOT / "model_registry.json"
    document = json.loads(registry_path.read_text(encoding="utf-8"))
    registrations = tuple(ModelRegistration.from_dict(row) for row in document["models"])
    model_ids = [row.model_id for row in registrations]
    if len(model_ids) != len(set(model_ids)):
        raise ValueError("模型注册表存在重复model_id")
    for registration in registrations:
        if registration.spec_file and not (MODEL_SPEC_ROOT / registration.spec_file).is_file():
            raise FileNotFoundError(f"模型规格文件不存在：{registration.spec_file}")
    return registrations


def find_model_registration(model_id: str) -> ModelRegistration | None:
    return next(
        (registration for registration in load_model_registry() if registration.model_id == model_id),
        None,
    )


def require_released_model(model_id: str) -> ModelRegistration:
    registration = find_model_registration(model_id)
    if registration is None:
        raise ModelNotReadyError(f"模型未登记：{model_id}")
    if not registration.released:
        blockers = "；".join(registration.release_blockers) or "模型尚未发布"
        raise ModelNotReadyError(
            f"模型{model_id}当前状态为{registration.status}，不能用于正式报告：{blockers}"
        )
    return registration


__all__ = [
    "MODEL_SPEC_ROOT",
    "ModelRegistration",
    "find_model_registration",
    "load_model_registry",
    "require_released_model",
]

"""Persistence-neutral workflow state used for restart-safe stage-four steps."""

from __future__ import annotations

from typing import Protocol

from .contracts import StepResult


class WorkflowStore(Protocol):
    def load_step(self, job_id: str, step: str, input_sha256: str) -> StepResult | None: ...

    def save_step(self, job_id: str, result: StepResult) -> None: ...

    def is_cancel_requested(self, job_id: str) -> bool: ...


class InMemoryWorkflowStore:
    def __init__(self) -> None:
        self._steps: dict[tuple[str, str], StepResult] = {}
        self._cancelled: set[str] = set()

    def load_step(self, job_id: str, step: str, input_sha256: str) -> StepResult | None:
        result = self._steps.get((job_id, step))
        if result is None or result.input_sha256 != input_sha256:
            return None
        return result

    def save_step(self, job_id: str, result: StepResult) -> None:
        previous = self._steps.get((job_id, result.step))
        if previous is not None and previous.input_sha256 == result.input_sha256:
            if previous.output_sha256 != result.output_sha256:
                raise ValueError("相同步骤输入不得固化不同输出")
            return
        self._steps[(job_id, result.step)] = result

    def is_cancel_requested(self, job_id: str) -> bool:
        return job_id in self._cancelled

    def request_cancel(self, job_id: str) -> None:
        self._cancelled.add(job_id)


__all__ = ["InMemoryWorkflowStore", "WorkflowStore"]

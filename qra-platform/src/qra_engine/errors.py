from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    path: str
    message: str
    severity: str = "ERROR"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class InputValidationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = tuple(issues)
        summary = "; ".join(f"{issue.code}: {issue.message}" for issue in issues)
        super().__init__(summary)


class ModelNotReadyError(RuntimeError):
    """请求的模型已经登记，但尚未完成规格冻结或代码实现。"""


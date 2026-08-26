from __future__ import annotations

import hashlib
import json
import math
from typing import Any


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def canonicalize_numerical_result(value: Any) -> Any:
    """Normalize non-semantic ordering and floating aggregation noise for hashing."""
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("数值结果哈希不接受NaN或Infinity")
        if value == 0.0:
            return 0.0
        return float(format(value, ".12g"))
    if isinstance(value, dict):
        return {
            str(key): canonicalize_numerical_result(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if str(key)
            not in {
                "result_sha256",
                "numerical_result_sha256",
                "audit_manifest_sha256",
                "frequency_balance_error",
            }
        }
    if isinstance(value, (list, tuple)):
        normalized = [canonicalize_numerical_result(item) for item in value]
        # Result-record arrays and label arrays are sets keyed by fields within
        # each item. Pure numeric arrays (coordinates, curve data pairs) retain
        # positional meaning and therefore keep their original order.
        if all(isinstance(item, dict) for item in normalized) or all(
            isinstance(item, str) for item in normalized
        ):
            return sorted(
                normalized,
                key=lambda item: canonical_json_bytes(item),
            )
        return normalized
    raise TypeError(f"数值结果包含不支持的类型：{type(value).__name__}")


def sha256_numerical_result(value: Any) -> str:
    return sha256_json(canonicalize_numerical_result(value))

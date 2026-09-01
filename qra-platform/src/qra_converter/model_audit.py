"""Small provider-neutral callback contract for attempt-level model audit."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

ModelAuditCallback = Callable[[dict[str, Any]], None]

_SECRET = re.compile(r"(?:sk-[A-Za-z0-9_-]+|Bearer\s+[A-Za-z0-9._-]+)", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s)\]}]+", re.IGNORECASE)
_ABSOLUTE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|/)(?:[^\s:]+[\\/])*[^\s:]*")
_DATA_URI = re.compile(r"data:image/[^;,]+;base64,[A-Za-z0-9+/=]+", re.IGNORECASE)


def sanitized_error_message(value: object, *, limit: int = 300) -> str:
    """Keep a stable diagnostic without credentials, URLs, paths or document bodies."""

    text = " ".join(str(value).split())
    text = _SECRET.sub("[REDACTED_SECRET]", text)
    text = _URL.sub("[REDACTED_URL]", text)
    text = _ABSOLUTE_PATH.sub("[REDACTED_PATH]", text)
    text = _DATA_URI.sub("[REDACTED_MEDIA]", text)
    # Provider messages after the first sentence are commonly request echoes.
    text = text.split("\n", 1)[0]
    return text[:limit]


__all__ = ["ModelAuditCallback", "sanitized_error_message"]

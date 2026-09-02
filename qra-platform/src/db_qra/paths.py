"""Runtime paths for the database-backed application.

Only this module knows the repository's local workspace layout. Deployments can
override it with ``QRA_WORKSPACE_ROOT`` without changing package code.
"""

from __future__ import annotations

import os
from pathlib import Path

_configured_project_root = os.environ.get("QRA_PROJECT_ROOT")
PROJECT_ROOT = (
    Path(_configured_project_root).resolve()
    if _configured_project_root
    else Path(__file__).resolve().parents[2]
)
_configured_workspace = os.environ.get("QRA_WORKSPACE_ROOT")
WORKSPACE_ROOT = (
    Path(_configured_workspace).resolve()
    if _configured_workspace
    else PROJECT_ROOT / "workspace"
)
DEFAULT_DATABASE = WORKSPACE_ROOT / "state" / "qra.sqlite3"
DEFAULT_RUNTIME_ROOT = WORKSPACE_ROOT / "runtime"


__all__ = [
    "DEFAULT_DATABASE",
    "DEFAULT_RUNTIME_ROOT",
    "PROJECT_ROOT",
    "WORKSPACE_ROOT",
]

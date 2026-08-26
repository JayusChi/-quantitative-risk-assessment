"""Enforce dependency direction between the platform's bounded contexts."""

from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src"
FORBIDDEN_IMPORTS = {
    "qra_engine": {"db_qra", "qra_converter"},
    "qra_converter": {"db_qra", "qra_engine"},
    "db_qra": set(),
}


def imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def main() -> int:
    violations: list[str] = []
    for package, forbidden in FORBIDDEN_IMPORTS.items():
        for path in sorted((SOURCE_ROOT / package).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            relative = path.relative_to(PROJECT_ROOT)
            if "sys.path." in source:
                violations.append(f"{relative}: 源码包不得修改 sys.path")
            tree = ast.parse(source, filename=str(path))
            illegal = sorted(imported_roots(tree) & forbidden)
            if illegal:
                violations.append(f"{relative}: 禁止依赖 {', '.join(illegal)}")

    if violations:
        print("ARCHITECTURE CHECK FAILED")
        for violation in violations:
            print(f"- {violation}")
        return 1
    print("ARCHITECTURE CHECK PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


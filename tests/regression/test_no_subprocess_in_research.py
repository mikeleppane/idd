"""Static guard: no subprocess shell-out from any ``tools/research/`` module.

The research pipeline is a pure read-only scan over the workspace; per
the M8 spec §5.3.10 it must never spawn a child process. AST walks every
``tools/research/**/*.py`` module and refuses any ``import subprocess``
or ``from subprocess import …``. The check is symmetric with the
cross-AI guard in ``test_no_subprocess_in_cross_ai_modules`` — there the
auto-mode dispatcher carves out an exemption; here no exemption exists,
so the entire subtree must remain subprocess-free.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_RESEARCH_ROOT: Path = _REPO_ROOT / "tools" / "research"


def _iter_research_modules() -> list[Path]:
    modules: list[Path] = []
    for path in _RESEARCH_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        modules.append(path)
    return modules


def _scan_subprocess(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    failures: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            failures.extend(
                f"{path}:{node.lineno}: import {alias.name}"
                for alias in node.names
                if alias.name == "subprocess" or alias.name.startswith("subprocess.")
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "subprocess" or module.startswith("subprocess."):
                failures.append(f"{path}:{node.lineno}: from {module} import …")
    return failures


def test_no_subprocess_imports_in_research_modules() -> None:
    """``tools/research/**`` must never import ``subprocess``."""
    failures: list[str] = []
    for module in sorted(_iter_research_modules()):
        failures.extend(_scan_subprocess(module))
    assert not failures, "subprocess import detected under tools/research/:\n  " + "\n  ".join(
        failures
    )

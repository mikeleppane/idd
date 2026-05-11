"""Static guard: no module under ``tools/`` may import the MCP transport.

Per the M8 spec §5.3.10, the FORGE tool surface stays free of any
``mcp__*`` import or bare ``WebSearch`` reference. The contract boundary
is enforced by this AST + textual scan so a future regression cannot
silently smuggle a managed-agent transport into the validator / research
pipeline.

Detection rules:

* AST walk every ``tools/**/*.py`` (excluding ``__pycache__`` and any
  ``tests/`` subtree).
* Reject any ``Import`` or ``ImportFrom`` whose module / alias name
  begins with ``mcp__``.
* Reject any source line containing the bare identifier ``WebSearch``
  outside a ``#`` comment line, an indented docstring, or a fenced
  triple-quoted block. The rough-cut is intentional: the MCP transport
  surface is small enough that even one accidental hit warrants a
  failed assertion.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_TOOLS_ROOT: Path = _REPO_ROOT / "tools"

_MCP_PREFIX = "mcp__"
_WEBSEARCH_TOKEN = "WebSearch"  # noqa: S105 — banned identifier, not a credential


def _iter_tool_modules() -> list[Path]:
    """Yield every ``*.py`` under ``tools/`` excluding tests + cache dirs."""
    modules: list[Path] = []
    for path in _TOOLS_ROOT.rglob("*.py"):
        parts = set(path.parts)
        if "__pycache__" in parts or "tests" in parts:
            continue
        modules.append(path)
    return modules


def _scan_imports(path: Path) -> list[str]:
    """Return error messages for ``mcp__*`` imports detected via AST."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    failures: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            failures.extend(
                f"{path}:{node.lineno}: import {alias.name}"
                for alias in node.names
                if alias.name.startswith(_MCP_PREFIX)
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(_MCP_PREFIX):
                failures.append(f"{path}:{node.lineno}: from {module} import …")
            failures.extend(
                f"{path}:{node.lineno}: from … import {alias.name}"
                for alias in node.names
                if alias.name.startswith(_MCP_PREFIX)
            )
    return failures


def _strip_triple_quoted(text: str) -> str:
    """Drop content inside ``\"\"\"...\"\"\"`` blocks so docstrings are exempt.

    Splits on ``\"\"\"`` and keeps every other chunk — the chunks that are
    OUTSIDE the triple-quoted regions. This is a coarse heuristic (it does
    not understand single-quoted ``'''`` blocks or escaped quotes) but the
    audited code uses double-quoted docstrings exclusively, so the cheap
    split avoids the cost of a full lexer pass.
    """
    chunks = text.split('"""')
    return "\n".join(chunks[::2])


def _scan_websearch(path: Path) -> list[str]:
    """Return error messages for bare ``WebSearch`` tokens outside docstrings."""
    text = path.read_text(encoding="utf-8")
    cleaned = _strip_triple_quoted(text)
    failures: list[str] = []
    for line_no, line in enumerate(cleaned.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if _WEBSEARCH_TOKEN in line:
            failures.append(f"{path}:{line_no}: bare WebSearch token: {line.rstrip()}")
    return failures


def test_no_mcp_imports_under_tools() -> None:
    """``tools/**`` must never import an ``mcp__*`` symbol."""
    failures: list[str] = []
    for module in sorted(_iter_tool_modules()):
        failures.extend(_scan_imports(module))
    assert not failures, "mcp__ import detected under tools/:\n  " + "\n  ".join(failures)


def test_no_websearch_token_under_tools() -> None:
    """``tools/**`` must never reference the ``WebSearch`` MCP transport."""
    failures: list[str] = []
    for module in sorted(_iter_tool_modules()):
        failures.extend(_scan_websearch(module))
    assert not failures, "WebSearch token detected under tools/:\n  " + "\n  ".join(failures)

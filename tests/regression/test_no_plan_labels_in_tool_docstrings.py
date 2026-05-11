"""Regression lock: cross-AI / research / redaction modules carry no plan labels.

Historical scope creep let plan-stage shorthand (``M3``, ``P2.1``,
``deep-M-A2``) leak into module docstrings, function docstrings, and
``#`` comments. The labels make sense in the planning directory but not
in the runtime codebase: they age out, they reference docs the runtime
reader does not have, and they violate the contributor convention that
forbids internal phase / finding refs in production code.

This regression scans the cross-AI substrate, the research grounding
pipeline, and the shared redaction filter (the modules whose docstrings
were swept clean as part of the same audit). Other modules under
``tools/`` keep legitimate legacy refs (e.g. ``tools.state``,
``tools.archive``) and are intentionally out of scope here so the lock
fails noisily on the modules that should stay clean.

Detection:

* AST-walk every ``*.py`` under the in-scope tree.
* Scan the module docstring + every function/class docstring.
* Scan every ``#`` comment line in the source.
* Reject any of these substring patterns (word-bounded for ``M`` /
  ``P`` so identifiers like ``M_PI`` do not false-trip):

  - ``M[0-9]+``    (e.g. ``M3``, ``M8``)
  - ``P[0-6]``     (optionally ``.N``; e.g. ``P0``, ``P3.4``)
  - ``deep-M-A2`` / ``deep-P-B1`` style spec-deep refs.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_TOOLS_ROOT: Path = _REPO_ROOT / "tools"

# Modules whose docstrings + comments must stay free of plan-stage labels.
_IN_SCOPE_TREES: tuple[Path, ...] = (
    _TOOLS_ROOT / "cross_ai",
    _TOOLS_ROOT / "research",
)
_IN_SCOPE_FILES: tuple[Path, ...] = (_TOOLS_ROOT / "redaction.py",)

# Patterns scanned against docstrings + ``#`` comments. Word boundaries
# guard against false positives like ``M_PI`` constants or English words
# that happen to contain ``Mn`` (none in scope today, but the bound keeps
# the lock cheap to maintain).
_FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Mn label", re.compile(r"\bM\d+\b")),
    ("Pn label", re.compile(r"\bP[0-6](?:\.[0-9]+)?\b")),
    ("deep-X-YN label", re.compile(r"\bdeep-[A-Z]-[A-Z]\d+\b")),
)


def _iter_in_scope_modules() -> list[Path]:
    """Return every ``*.py`` file under the in-scope trees / file list."""
    modules: list[Path] = []
    for tree in _IN_SCOPE_TREES:
        for path in tree.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            modules.append(path)
    modules.extend(_IN_SCOPE_FILES)
    return sorted(modules)


def _docstrings(tree: ast.AST) -> list[str]:
    """Pull module + every function/class docstring out of an AST."""
    docs: list[str] = []
    module_doc = ast.get_docstring(tree, clean=False) if isinstance(tree, ast.Module) else None
    if module_doc is not None:
        docs.append(module_doc)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docs.append(doc)
    return docs


def _comment_lines(source: str) -> list[str]:
    """Return every line that begins (after whitespace) with ``#``."""
    out: list[str] = []
    for line in source.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            out.append(stripped)
    return out


def _scan_text(label: str, where: str, text: str) -> list[str]:
    """Return one failure entry per pattern hit in ``text``."""
    failures: list[str] = []
    for desc, pattern in _FORBIDDEN_PATTERNS:
        failures.extend(
            f"{label}:{where}: {desc} {match.group(0)!r}" for match in pattern.finditer(text)
        )
    return failures


def test_no_plan_labels_in_in_scope_module_docstrings_or_comments() -> None:
    """No ``M3`` / ``P2.1`` / ``deep-M-A2`` shorthand in the swept modules."""
    failures: list[str] = []
    for module in _iter_in_scope_modules():
        rel = module.relative_to(_REPO_ROOT)
        source = module.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(module))
        except SyntaxError as exc:  # pragma: no cover - guard against future syntax churn
            failures.append(f"{rel}: parse error {exc}")
            continue
        for doc in _docstrings(tree):
            failures.extend(_scan_text(str(rel), "docstring", doc))
        for comment in _comment_lines(source):
            failures.extend(_scan_text(str(rel), "comment", comment))
    assert not failures, "plan-stage labels detected:\n  " + "\n  ".join(failures)

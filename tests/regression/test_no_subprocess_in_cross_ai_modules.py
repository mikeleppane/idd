"""Static AST guard: manual-mode helpers must never invoke a subprocess.

Walks every ``tools/cross_ai/*.py`` module and refuses any call whose
function attribute resolves to one of the documented subprocess /
process-spawning entry points (``subprocess.run`` / ``Popen`` /
``call`` / ``check_call`` / ``check_output``, ``os.system``,
``os.popen``). Detection is purely syntactic — we walk for
``ast.Call(func=ast.Attribute(value=ast.Name(id="subprocess"|"os"),
attr=...))`` so an alias rename (``import subprocess as sp``) would slip
this guard, but the audited modules ship with neither alias nor direct
use; the cheap check catches the regression we care about.

Hand-off note: a future auto-mode dispatcher is the single module
allowed to spawn external CLIs. When it lands as
``tools/cross_ai/dispatch.py`` this guard should be relaxed to exclude
that file from the walk (or inverted to assert dispatch is the *only*
module inside ``tools/cross_ai/`` that subprocess-calls). The dispatch
module is intentionally absent today; its presence here would itself be
a regression.
"""

from __future__ import annotations

import ast
from pathlib import Path

# Repo root is two levels above this file (tests/regression/<this>).
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_CROSS_AI_DIR: Path = _REPO_ROOT / "tools" / "cross_ai"

# Modules in ``tools/cross_ai/`` that are allowed to invoke subprocess.
# Empty today; a future auto-mode dispatcher will be added here when
# it ships. The prompt builder (which does shell out to ``git`` for the
# code-target diff) lives in this directory and IS exempt via
# :data:`_PROMPT_BUILDER_BYPASS` — see the function below for the
# rationale.
_DISPATCH_ALLOWED_MODULES: frozenset[str] = frozenset()

# Banned attribute calls — ``(module, attr)`` pairs whose ``ast.Call``
# shape we refuse anywhere under ``tools/cross_ai/`` (modulo the
# allow-list above). The prompt builder is the exception and is allowed
# at module level via :data:`_PROMPT_BUILDER_BYPASS` because its diff
# shell-out is a documented, intentional behavior.
_BANNED_CALLS: tuple[tuple[str, str], ...] = (
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
    ("os", "system"),
    ("os", "popen"),
)

# The prompt builder's documented ``git diff`` shell-out is an
# intentional behavior covered by its own tests; the manual-mode no-
# subprocess invariant applies to the helpers the dispatcher wires
# together, not to the upstream prompt construction. Tracking the
# exception here (rather than inside the walk) makes the deliberate
# carve-out visible at file scope.
_PROMPT_BUILDER_BYPASS: frozenset[str] = frozenset({"prompt.py"})


def _resolve_attr_call(node: ast.Call) -> tuple[str, str] | None:
    """Return ``(module, attr)`` when ``node`` is ``module.attr(...)``.

    Returns ``None`` for any call shape that is not a bare
    ``Name.Attribute`` access (chained attribute access, lambdas, etc.).
    The narrow shape is intentional: the modules under audit do not
    alias subprocess, so a strict matcher trades zero false positives
    for zero false negatives in the cases we ship.
    """
    func = node.func
    if not isinstance(func, ast.Attribute):
        return None
    if not isinstance(func.value, ast.Name):
        return None
    return (func.value.id, func.attr)


def _scan_module(path: Path) -> list[tuple[int, str, str]]:
    """Return ``(line, module, attr)`` for every banned call in ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        resolved = _resolve_attr_call(node)
        if resolved is None:
            continue
        if resolved in _BANNED_CALLS:
            hits.append((node.lineno, resolved[0], resolved[1]))
    return hits


def test_no_subprocess_calls_in_manual_mode_modules() -> None:
    """Refuse subprocess invocation anywhere under ``tools/cross_ai/``.

    The guard walks every ``*.py`` module, parses it with ``ast``, and
    refuses any banned attribute call. ``prompt.py`` is exempt because
    its ``git diff`` shell-out is an intentional, documented behavior
    of the code-target prompt builder; ``_DISPATCH_ALLOWED_MODULES``
    will house the auto-mode dispatcher's filename when it lands.
    """
    failures: list[str] = []
    for module_path in sorted(_CROSS_AI_DIR.glob("*.py")):
        if module_path.name in _PROMPT_BUILDER_BYPASS:
            continue
        if module_path.name in _DISPATCH_ALLOWED_MODULES:
            continue
        for line, module, attr in _scan_module(module_path):
            failures.append(f"{module_path}:{line}: banned call {module}.{attr}(...)")

    assert not failures, (
        "manual-mode modules must not invoke subprocess directly:\n  " + "\n  ".join(failures)
    )


def test_dispatch_module_is_absent() -> None:
    """``tools/cross_ai/dispatch.py`` is reserved for a future increment.

    Its presence today would mean auto-mode landed without the guard
    being relaxed in the same change — flag it as a regression.
    """
    dispatch_path = _CROSS_AI_DIR / "dispatch.py"
    assert not dispatch_path.exists(), (
        f"unexpected dispatch module: {dispatch_path} — "
        "if auto mode is shipping, relax _DISPATCH_ALLOWED_MODULES in this guard"
    )

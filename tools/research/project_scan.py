"""Assembly layer that ties the ecosystem detector to library_extract.

The research subagent needs a single call that answers four questions
about the repo:

* which ecosystems are present (excluding the generic safety-net);
* what are the declared deps per ecosystem (already canonicalized);
* a deduplicated, normalized union of all declared deps across
  ecosystems (the basis of the Context7/WebSearch lookup list);
* a top-level layout summary (excluding hidden + vendor + build dirs)
  and a best-effort entrypoint per ecosystem.

This module is the only place where ecosystem-detector output is
post-processed for the subagent. Skill prose calls
``project_scan.scan(repo_root)`` and renders the result as the budget
block in RESEARCH.md — the prose stays free of language-specific
manifest names per the detector-abstraction contract.

The generic plugin always matches but exposes none of the manifest /
declared-deps surface (the parity test in the ecosystems package
exempts it). We filter it out of the public ``ecosystems`` tuple here
so callers never have to special-case the safety net.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from tools.research import library_extract
from tools.research.ecosystem import detect

_LAYOUT_EXCLUDED: frozenset[str] = frozenset(
    {
        "__pycache__",
        "node_modules",
        "target",
        "build",
        "dist",
        ".venv",
        "venv",
        ".tox",
        "vendor",
    }
)
_TOP_MODULES_LIMIT: int = 5


@dataclass(frozen=True)
class ScanResult:
    """Aggregate summary of a repo's research-relevant surface.

    Attributes:
        ecosystems: Names of ecosystem-specific plugins that matched,
            in detection order. The generic safety-net plugin is
            filtered out — callers that need to know "no ecosystem
            matched" check ``ecosystems == ()``.
        entrypoints: Mapping of ecosystem name to a best-effort
            repo-relative entrypoint path. Empty string when no
            convention applies or when nothing was found.
        top_modules: First ``_TOP_MODULES_LIMIT`` declared deps per
            ecosystem (order-preserved). Used by the skill to seed
            the Context7 lookup list when the budget is tight.
        declared_deps: Full normalized declared-deps tuple per
            ecosystem (already canonicalized by the plugin).
        layout: Sorted top-level directory names, excluding hidden
            entries and the vendor/build dirs in
            :data:`_LAYOUT_EXCLUDED`.
        unioned_libraries: Deduped + normalized union of every
            ecosystem's declared deps. The canonical input list for
            the Context7/WebSearch lookup pass.
    """

    ecosystems: tuple[str, ...]
    entrypoints: dict[str, str]
    top_modules: dict[str, tuple[str, ...]]
    declared_deps: dict[str, tuple[str, ...]]
    layout: tuple[str, ...]
    unioned_libraries: tuple[str, ...]


def scan(
    repo_root: Path,
    *,
    pinned_ecosystems: list[str] | None = None,
) -> ScanResult:
    """Run the ecosystem detector and assemble a single ``ScanResult``.

    Args:
        repo_root: Path to the repository to scan.
        pinned_ecosystems: Optional list passed through to
            :func:`tools.research.ecosystem.detect`. When provided,
            the generic safety-net does not engage even if no plugin
            matches (consistent with the detector's pin semantics).

    Returns:
        :class:`ScanResult` with the generic safety-net filtered out
        of the public ``ecosystems`` tuple.
    """
    records = detect(repo_root, pinned=pinned_ecosystems)
    concrete = [r for r in records if r.name != "generic"]

    declared: dict[str, tuple[str, ...]] = {}
    top: dict[str, tuple[str, ...]] = {}
    entrypoints: dict[str, str] = {}

    for record in concrete:
        deps = record.declared_deps
        declared[record.name] = deps
        top[record.name] = deps[:_TOP_MODULES_LIMIT]
        entrypoints[record.name] = _entrypoint_for(record.name, repo_root)

    union = tuple(library_extract.dedup(name for deps in declared.values() for name in deps))

    return ScanResult(
        ecosystems=tuple(r.name for r in concrete),
        entrypoints=entrypoints,
        top_modules=top,
        declared_deps=declared,
        layout=_layout(repo_root),
        unioned_libraries=union,
    )


def _layout(repo_root: Path) -> tuple[str, ...]:
    """Return sorted top-level directory names with vendor/build excluded.

    Best-effort: a missing or unreadable ``repo_root`` collapses to the
    empty tuple rather than raising — the skill should still render a
    coherent budget block when the workspace is in flux.
    """
    try:
        entries = list(repo_root.iterdir())
    except OSError:
        return ()
    names = [
        entry.name
        for entry in entries
        if entry.is_dir() and not entry.name.startswith(".") and entry.name not in _LAYOUT_EXCLUDED
    ]
    return tuple(sorted(names))


def _entrypoint_for(ecosystem: str, repo_root: Path) -> str:
    """Return a best-effort repo-relative entrypoint for ``ecosystem``.

    Returns an empty string when no convention applies or no candidate
    file is present. Only python / node / rust / go have non-trivial
    entrypoint conventions worth probing here; everything else surfaces
    as an empty string rather than guessing.
    """
    if ecosystem == "python":
        return _python_entrypoint(repo_root)
    if ecosystem == "node":
        return _node_entrypoint(repo_root)
    if ecosystem == "rust":
        return "src/main.rs" if (repo_root / "src" / "main.rs").is_file() else ""
    if ecosystem == "go":
        return _go_entrypoint(repo_root)
    return ""


def _python_entrypoint(repo_root: Path) -> str:
    """Probe canonical Python entrypoints in priority order."""
    if (repo_root / "__main__.py").is_file():
        return "__main__.py"
    src = repo_root / "src"
    if src.is_dir():
        for candidate in sorted(src.iterdir()):
            if candidate.is_dir() and (candidate / "__main__.py").is_file():
                return f"src/{candidate.name}/__main__.py"
    if (repo_root / "setup.py").is_file():
        return "setup.py"
    return ""


def _node_entrypoint(repo_root: Path) -> str:
    """Read ``package.json:main`` first, then fall back to ``index.js``."""
    package_json = repo_root / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict):
            main = data.get("main")
            if isinstance(main, str) and main:
                return main
    if (repo_root / "index.js").is_file():
        return "index.js"
    return ""


def _go_entrypoint(repo_root: Path) -> str:
    """Return the first ``cmd/<name>/main.go`` discovered (sorted)."""
    cmd = repo_root / "cmd"
    if not cmd.is_dir():
        return ""
    for candidate in sorted(cmd.iterdir()):
        main_go = candidate / "main.go"
        if candidate.is_dir() and main_go.is_file():
            return f"cmd/{candidate.name}/main.go"
    return ""

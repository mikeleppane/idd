"""Shared filesystem walk helpers for ecosystem plugins.

Plugins all need to (a) read root manifests and (b) sweep source files
for import statements without descending into vendored / generated
trees. Centralising the exclusion set + the walk loop here keeps each
plugin's ``scan_imports`` short and prevents drift between the eleven
language families.
"""

import re
from collections.abc import Iterable, Iterator
from pathlib import Path

EXCLUDED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        "target",
        "build",
        "dist",
        "vendor",
        ".gradle",
        ".dart_tool",
        ".pub-cache",
        ".bundle",
        "out",
        "bin",
        "obj",
    }
)


def iter_source_files(repo_root: Path, suffixes: Iterable[str]) -> Iterator[Path]:
    """Yield repo-relative source files matching any of ``suffixes``.

    Walk contract:

    * Skips directories listed in :data:`EXCLUDED_DIR_NAMES` (matched by
      name at any depth).
    * Skips every directory whose basename starts with ``.`` (except the
      repo root itself). This keeps ``.git`` / ``.venv`` / ``.cache`` /
      hand-rolled hidden dirs out of the scan.
    * Refuses to descend into symlinked directories whose resolved target
      is **not** inside ``repo_root`` — the walk is a project-scan, so
      pulling files from outside the repo would violate the bounded
      research-phase contract.
    * Tracks already-visited resolved directories to avoid infinite loops
      from in-tree symlink cycles.
    """
    suffix_set = {s.lower() for s in suffixes}
    if not repo_root.is_dir():
        return
    try:
        root_resolved = repo_root.resolve()
    except OSError:
        return
    stack: list[Path] = [repo_root]
    visited: set[Path] = set()
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            if entry.name in EXCLUDED_DIR_NAMES:
                continue
            yield from _handle_entry(entry, suffix_set, root_resolved, stack, visited)


def _handle_entry(
    entry: Path,
    suffix_set: set[str],
    root_resolved: Path,
    stack: list[Path],
    visited: set[Path],
) -> Iterator[Path]:
    """Classify one ``iterdir`` entry: enqueue safe dirs, yield matching files."""
    try:
        is_dir = entry.is_dir()
    except OSError:
        return
    if is_dir:
        _maybe_enqueue_dir(entry, root_resolved, stack, visited)
        return
    if entry.suffix.lower() in suffix_set:
        yield entry


def _maybe_enqueue_dir(
    entry: Path,
    root_resolved: Path,
    stack: list[Path],
    visited: set[Path],
) -> None:
    """Push ``entry`` onto ``stack`` when the safety checks pass.

    Containment + hidden-name + cycle guards live here so
    :func:`iter_source_files` stays under the project's branch-count cap.
    """
    if entry.name.startswith("."):
        return
    try:
        resolved = entry.resolve()
    except OSError:
        return
    if not _is_within(resolved, root_resolved):
        return
    if resolved in visited:
        return
    visited.add(resolved)
    stack.append(entry)


def _is_within(candidate: Path, root: Path) -> bool:
    """Return True when ``candidate`` is ``root`` itself or a descendant.

    ``Path.is_relative_to`` exists on 3.9+ but raises on Windows mixed-case
    edge cases; the explicit ``parts`` comparison is portable and cheap.
    """
    if candidate == root:
        return True
    return root in candidate.parents


def scan_with_regex(
    repo_root: Path,
    suffixes: Iterable[str],
    pattern: re.Pattern[str],
    *,
    max_files: int = 2000,
) -> tuple[str, ...]:
    """Run ``pattern`` over each source file and return deduped lowercase matches.

    Args:
        repo_root: Repository root to walk.
        suffixes: File extensions (with leading dot) to scan.
        pattern: Compiled regex with a single capturing group; the
            captured value is lowercased and added to the result set.
        max_files: Hard ceiling on the number of files scanned to keep
            the walk cheap on large repos.

    Returns:
        Tuple of deduped lowercase names in first-seen order. Returns
        ``()`` on any unexpected error so plugins can stay best-effort.
    """
    seen: dict[str, None] = {}
    try:
        count = 0
        for path in iter_source_files(repo_root, suffixes):
            if count >= max_files:
                break
            count += 1
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for match in pattern.finditer(text):
                name = match.group(1).lower()
                if name and name not in seen:
                    seen[name] = None
    except (OSError, ValueError):
        return ()
    return tuple(seen)


def normalize_dep(name: str) -> str:
    """Lowercase + hyphen→underscore normalisation used across plugins."""
    return name.lower().replace("-", "_")

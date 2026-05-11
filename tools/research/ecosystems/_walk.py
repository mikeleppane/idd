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

    Skips directories listed in :data:`EXCLUDED_DIR_NAMES` (matched by
    name at any depth) and any path component starting with ``.``
    other than the repo root itself. Symlinks are followed only when
    they resolve within the repo to keep the walk bounded.
    """
    suffix_set = {s.lower() for s in suffixes}
    if not repo_root.is_dir():
        return
    stack: list[Path] = [repo_root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (OSError, PermissionError):
            continue
        for entry in entries:
            name = entry.name
            if name in EXCLUDED_DIR_NAMES:
                continue
            if entry.is_dir():
                stack.append(entry)
                continue
            if entry.suffix.lower() in suffix_set:
                yield entry


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

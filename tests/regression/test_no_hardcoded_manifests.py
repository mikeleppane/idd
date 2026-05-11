"""Static guard: no hardcoded manifest filenames in the research skill prose.

Per the M8 spec §5.3.12, the research skill must not bake a manifest
inventory into prose. Manifest filenames (``pyproject.toml``,
``package.json``, etc.) are an implementation detail of
``tools.research.ecosystem.detect()`` and the per-ecosystem plugins under
``tools.research.ecosystems``. If a skill author hand-rolled them into
the SKILL.md the lock would drift — this test traps that drift before
it ships.

The skill directory does not exist yet; the lock activates as soon as
``skills/forge-research/`` lands. Allowed contexts for a manifest token
on a prose line are:

* a fenced code block tagged ``text``;
* an HTML comment (``<!-- ... -->``);
* an inline backtick reference to ``tools.research.ecosystem.detect()`` or
  ``tools.research.ecosystems`` on the same line;
* a Markdown table row (heuristic: the line contains a ``|`` delimiter).
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_SKILL_ROOT: Path = _REPO_ROOT / "skills" / "forge-research"

_MANIFEST_TOKENS: tuple[str, ...] = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "pom.xml",
    ".csproj",
    "mix.exs",
    "composer.json",
    "Package.swift",
    "pubspec.yaml",
)

_ALLOWED_INLINE_REFS: tuple[str, ...] = (
    "tools.research.ecosystem.detect()",
    "tools.research.ecosystems",
)


def _line_is_allowed(line: str, *, in_text_fence: bool) -> bool:
    if in_text_fence:
        return True
    stripped = line.strip()
    if stripped.startswith("<!--") or stripped.endswith("-->"):
        return True
    if any(ref in line for ref in _ALLOWED_INLINE_REFS):
        return True
    return "|" in line


def _scan_markdown(path: Path) -> list[str]:
    """Return ``"<file>:<line>: <text>"`` for every offending manifest mention."""
    failures: list[str] = []
    in_text_fence = False
    in_other_fence = False
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("```"):
            tag = stripped[3:].strip().lower()
            if in_text_fence:
                in_text_fence = False
            elif in_other_fence:
                in_other_fence = False
            elif tag == "text":
                in_text_fence = True
            else:
                in_other_fence = True
            continue
        if in_other_fence:
            # Code fences not tagged ``text`` are presumed to be code samples
            # (e.g., bash, python). The spec doesn't permit manifest names
            # in those either; fall through to the allowance check.
            pass
        if not any(token in line for token in _MANIFEST_TOKENS):
            continue
        if _line_is_allowed(line, in_text_fence=in_text_fence):
            continue
        failures.append(f"{path.relative_to(_REPO_ROOT)}:{line_no}: {line.rstrip()}")
    return failures


def test_no_hardcoded_manifest_names_in_research_skill() -> None:
    """``skills/forge-research/**/*.md`` must route manifests through the detector.

    The skill ships in a later milestone phase; until the directory exists
    the lock skips. The moment the skill lands the lock activates and any
    raw manifest mention outside the allowed contexts will fail this test.
    """
    if not _SKILL_ROOT.is_dir():
        pytest.skip(f"{_SKILL_ROOT.relative_to(_REPO_ROOT)} not present yet")

    failures: list[str] = []
    for path in sorted(_SKILL_ROOT.rglob("*.md")):
        failures.extend(_scan_markdown(path))
    assert not failures, "hardcoded manifest names in research skill prose:\n  " + "\n  ".join(
        failures
    )

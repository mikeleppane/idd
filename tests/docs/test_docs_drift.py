"""Drift tests: user-facing docs must agree with canonical project state."""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _phases_from_schema() -> set[str]:
    schema = json.loads((REPO_ROOT / "schemas" / "state.schema.json").read_text(encoding="utf-8"))
    enum = schema["properties"]["phases"]["propertyNames"]["enum"]
    return set(enum)


def _commands_on_disk() -> set[str]:
    return {p.stem for p in (REPO_ROOT / "commands").glob("*.md")}


def _skills_on_disk() -> set[str]:
    return {p.parent.name for p in (REPO_ROOT / "skills").glob("*/SKILL.md")}


def test_readme_phase_list_matches_schema() -> None:
    """README lifecycle code block lists every phase in the state schema enum."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(
        r"^## Lifecycle\b.*?```text\s*(.*?)```",
        readme,
        re.DOTALL | re.MULTILINE,
    )
    assert m, "Lifecycle code block not found in README.md"
    found = set(re.findall(r"\b([a-z][a-z-]+)\b", m.group(1)))
    expected = _phases_from_schema()
    missing = expected - found
    extra = found - expected
    assert not missing and not extra, (
        f"README lifecycle phase drift - missing: {sorted(missing)}, extra: {sorted(extra)}"
    )


def test_readme_command_list_matches_commands_dir() -> None:
    """README mentions every command on disk; every /forge:X in README exists on disk."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    mentioned = set(re.findall(r"/forge:([a-z][a-z-]+)", readme))
    on_disk = _commands_on_disk()
    phantom = mentioned - on_disk
    missing = on_disk - mentioned
    assert not phantom, f"README references commands not on disk: {sorted(phantom)}"
    assert not missing, f"commands/ entries missing from README: {sorted(missing)}"


def test_agents_md_skill_citations_match_skills_dir() -> None:
    """AGENTS.md cites every skill on disk; every forge-* name in AGENTS.md exists on disk."""
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    cited = set(re.findall(r"\b(forge-[a-z-]+)\b", agents))
    on_disk = _skills_on_disk()
    phantom = cited - on_disk
    missing = on_disk - cited
    assert not phantom, f"AGENTS.md cites skills not on disk: {sorted(phantom)}"
    assert not missing, f"skills/ entries missing from AGENTS.md: {sorted(missing)}"

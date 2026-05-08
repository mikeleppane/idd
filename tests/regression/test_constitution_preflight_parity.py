"""Verify each migrated skill performs Constitution preflight when present.

These are skill-protocol assertions executed against the documentation: each
SKILL.md edited in Task 5 MUST contain the literal token
`tools.constitution.load_and_filter` so future readers (and reviewers) see
the contract uniformly. The runtime contract is enforced by the dispatch
hook (Task 2): subagent dispatches without articles[] when the loader
returned articles != [] are caught at hook layer in production.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SKILL_FILES = [
    "skills/forge-spec/SKILL.md",
    "skills/forge-scenarios/SKILL.md",
    "skills/forge-plan/SKILL.md",
    "skills/forge-crucible/SKILL.md",
    "skills/forge-execute/SKILL.md",
    "skills/forge-review/SKILL.md",
    "skills/forge-verify/SKILL.md",
]

COMMAND_FILES = [
    "commands/spec.md",
    "commands/scenarios.md",
    "commands/plan.md",
    "commands/crucible.md",
    "commands/execute.md",
    "commands/review.md",
    "commands/verify.md",
]


@pytest.mark.parametrize("skill_path", SKILL_FILES)
def test_skill_documents_constitution_preflight(skill_path: str) -> None:
    text = Path(skill_path).read_text(encoding="utf-8")
    assert "tools.constitution.load_and_filter" in text, (
        f"{skill_path} must document a Constitution preflight call to "
        "tools.constitution.load_and_filter"
    )


@pytest.mark.parametrize("skill_path", SKILL_FILES)
def test_skill_documents_articles_in_dispatch_budget(skill_path: str) -> None:
    text = Path(skill_path).read_text(encoding="utf-8")
    assert "articles[]" in text or "articles:" in text, (
        f"{skill_path} must mention articles[] or articles: in its subagent dispatch contract"
    )


@pytest.mark.parametrize("command_path", COMMAND_FILES)
def test_command_doc_mentions_constitution_preflight(command_path: str) -> None:
    text = Path(command_path).read_text(encoding="utf-8")
    assert "Constitution preflight" in text or "tools.constitution" in text, (
        f"{command_path} must mention Constitution preflight (M3 §5.3.1 / §6.4)"
    )


def test_forge_constitution_skill_exists() -> None:
    skill = Path("skills/forge-constitution/SKILL.md")
    assert skill.exists(), "forge-constitution skill must be authored in Task 5"
    text = skill.read_text(encoding="utf-8")
    assert "load_and_filter" in text
    assert "disable-model-invocation: true" in text


def test_readme_documents_m3_constitution_limitations() -> None:
    text = Path("README.md").read_text(encoding="utf-8")
    assert "M3 Constitution limitations" in text or "Constitution (M3)" in text, (
        "README must surface the advisory-only nature of M3 Constitution "
        "enforcement (D-4 risk disclosure)"
    )

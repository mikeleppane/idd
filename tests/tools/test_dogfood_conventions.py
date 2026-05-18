"""Dogfood checks for FORGE's own ``.forge/conventions.json`` + ``config.json``.

The repo design names AGENTS.md's "subagent dispatches MUST cite the four
engineering-practice skills" rule as the canonical case for the
``dispatch_brief`` hook mechanism, and locks the ``Co-Authored-By: Claude*``
trailer ban to the ``git_conventions.trailers.ban_patterns`` opt-in path.
These tests prove the FORGE repo itself ships the mechanisms, not just the
prose: the conventions file loads strict-clean, every cited skill matches a
real skill directory on disk, the dispatch hook actually fires on a brief
that omits a citation, and the git-conventions trailer-ban actually fires
on a commit message carrying the banned trailer.

Each test is repo-aware — they read the production files at
``REPO_ROOT/.forge/`` rather than synthesizing fixtures, because the
dogfood guarantee is "FORGE eats its own dog food", not "FORGE's helpers
work on synthetic input".
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import TYPE_CHECKING

from tools.conventions_runtime import load_conventions_permissive
from tools.validate._config_shape import validate_config
from tools.validate.conventions import load_conventions
from tools.validate.git_conventions import (
    _check_trailers,
    _compile_bans,
    _split_message,
    load_config,
)

if TYPE_CHECKING:
    from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
CONVENTIONS_PATH = REPO_ROOT / ".forge" / "conventions.json"
CONFIG_PATH = REPO_ROOT / ".forge" / "config.json"
HOOK = REPO_ROOT / "hooks" / "check_budget.py"


def _load_hook() -> ModuleType:
    """Side-load the hook module the same way the dispatch hook tests do."""
    spec = importlib.util.spec_from_file_location("check_budget", HOOK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_check_budget = _load_hook()


# Skill slugs cited on AGENTS.md line 82. Keep this list in sync with both
# the prose (AGENTS.md) and ``.forge/conventions.json``; the assertions
# below cross-check both directions.
EXPECTED_SKILL_SLUGS = (
    "test-driven-development",
    "coding-guidance-python",
    "git-conventions",
    "code-review-and-quality",
)


_VALID_BUDGET_BLOCK = (
    "context_budget:\n"
    "{\n"
    '  "files_in_scope": ["tools/state.py"],\n'
    '  "forbidden": ["read entire repo"]\n'
    "}\n"
)


# ---------------------------------------------------------------------------
# Conventions file shape + strict load
# ---------------------------------------------------------------------------


def test_conventions_file_exists() -> None:
    assert CONVENTIONS_PATH.is_file(), (
        f"FORGE must dogfood its own conventions: {CONVENTIONS_PATH} is missing"
    )


def test_conventions_file_loads_strict() -> None:
    """Strict load (schema + dup-id + regex + ReDoS-shape + scope) passes."""
    rules = load_conventions(REPO_ROOT)
    assert rules, "conventions.json must carry at least one rule"


def test_conventions_carries_one_rule_per_cited_skill() -> None:
    """One ``required_text`` rule per engineering-practice skill in AGENTS.md."""
    rules = load_conventions(REPO_ROOT)
    rule_patterns = {rule.pattern for rule in rules}
    for slug in EXPECTED_SKILL_SLUGS:
        assert slug in rule_patterns, (
            f"missing required_text rule for engineering-practice skill {slug!r}; "
            "AGENTS.md cites it but the convention does not"
        )


def test_conventions_engineering_rules_use_dispatch_brief_scope_with_high_severity() -> None:
    """Every engineering-citation rule fires on dispatch_brief with BLOCK or HIGH.

    Slice 4's dispatch-brief floor refuses MEDIUM/LOW/WARN for that scope —
    the strict loader would already raise — but pin the contract here so a
    future regression cannot weaken the rules silently while keeping the
    file schema-valid by accident.
    """
    rules = load_conventions(REPO_ROOT)
    for rule in rules:
        if rule.pattern not in EXPECTED_SKILL_SLUGS:
            continue
        assert rule.pattern_kind == "required_text", (
            f"rule {rule.id!r} must be required_text, got {rule.pattern_kind!r}"
        )
        assert rule.scope == ("dispatch_brief",), (
            f"rule {rule.id!r} must scope to dispatch_brief only, got {rule.scope!r}"
        )
        assert rule.severity in {"BLOCK", "HIGH"}, (
            f"rule {rule.id!r} severity must be BLOCK or HIGH, got {rule.severity!r}"
        )


def test_conventions_skill_slug_patterns_match_existing_skill_directories() -> None:
    """Every cited skill slug points at an actual ``.agents/skills/<slug>`` SKILL.md.

    A dispatch_brief rule whose pattern names a non-existent skill would
    silently demand a citation that authors cannot satisfy. Cross-check the
    convention against the skill directory layout so the dogfood does not
    rot when a skill is renamed.
    """
    skills_root = REPO_ROOT / ".agents" / "skills"
    rules = load_conventions(REPO_ROOT)
    for rule in rules:
        if rule.pattern not in EXPECTED_SKILL_SLUGS:
            continue
        skill_md = skills_root / rule.pattern / "SKILL.md"
        assert skill_md.is_file(), (
            f"convention rule {rule.id!r} cites skill slug {rule.pattern!r} "
            f"but {skill_md} does not exist"
        )


# ---------------------------------------------------------------------------
# Config file shape + trailer-ban surface
# ---------------------------------------------------------------------------


def test_config_file_exists() -> None:
    assert CONFIG_PATH.is_file(), (
        f"FORGE must dogfood the git_conventions opt-in: {CONFIG_PATH} is missing"
    )


def test_config_file_passes_shape_validator() -> None:
    findings = validate_config(CONFIG_PATH)
    assert findings == [], (
        "config.json shape validator must return zero findings; got: "
        + ", ".join(f.message for f in findings)
    )


def test_config_carries_co_authored_by_claude_ban_pattern() -> None:
    """The locked-decision-4 trailer ban must show up in trailer_ban_patterns."""
    cfg = load_config(REPO_ROOT)
    assert any("Claude" in pattern for pattern in cfg.trailer_ban_patterns), (
        "trailer_ban_patterns must contain a Co-Authored-By: Claude* pattern; "
        f"got {cfg.trailer_ban_patterns!r}"
    )


def test_config_subject_block_has_allowed_scopes() -> None:
    """The git-conventions subject block must list the scopes FORGE actually uses."""
    cfg = load_config(REPO_ROOT)
    assert cfg.require_conventional_commits is True
    # Sanity sample — current branch's recent commits use these scopes; do
    # not pin the full list (history grows), only the contract surface.
    for required in ("tools", "skills", "hooks", "tests", "docs"):
        assert required in cfg.allowed_scopes, (
            f"allowed_scopes must include {required!r} (used in recent history); "
            f"got {cfg.allowed_scopes!r}"
        )


# ---------------------------------------------------------------------------
# Dispatch hook fires on missing citations / allows when complete
# ---------------------------------------------------------------------------


def _cite(*slugs: str) -> str:
    """Build the citation line that a dispatch brief would carry."""
    return "Cite: " + ", ".join(slugs) + ".\n"


def test_dispatch_hook_denies_when_one_skill_citation_missing() -> None:
    """Omit a single required slug — hook denies, reason names the firing rule."""
    # Cite every slug EXCEPT test-driven-development. The corresponding
    # required_text rule must fire.
    citations = _cite(*[s for s in EXPECTED_SKILL_SLUGS if s != "test-driven-development"])
    prompt = _VALID_BUDGET_BLOCK + "\n" + citations
    allow, reason = _check_budget._check_dispatch_brief_conventions(prompt, repo_root=REPO_ROOT)
    assert not allow, "hook must deny when one of the four citations is missing"
    assert "test-driven-development" in reason or "agents-md-cite-test-driven" in reason


def test_dispatch_hook_allows_when_all_four_citations_present() -> None:
    """Cite all four — hook allows."""
    prompt = _VALID_BUDGET_BLOCK + "\n" + _cite(*EXPECTED_SKILL_SLUGS)
    allow, reason = _check_budget._check_dispatch_brief_conventions(prompt, repo_root=REPO_ROOT)
    assert allow, f"hook must allow when all four citations are present; reason={reason!r}"
    assert reason == "ok"


def test_dispatch_hook_permissive_loader_returns_four_engineering_rules() -> None:
    """The hook's stdlib-only loader sees every shipping engineering-citation rule.

    Strict and permissive loaders disagree on ``dispatch_brief`` dead-letter
    rules (the permissive path silently drops them); cross-check that none
    of the engineering rules end up in the dead-letter bucket.
    """
    rules = load_conventions_permissive(REPO_ROOT)
    patterns = {rule.pattern for rule in rules}
    for slug in EXPECTED_SKILL_SLUGS:
        assert slug in patterns, (
            f"permissive loader dropped the rule for {slug!r}; the hook would not enforce it"
        )


# ---------------------------------------------------------------------------
# git-conventions trailer ban actually fires
# ---------------------------------------------------------------------------


_COAUTHOR_MESSAGE_WITH_TRAILER = (
    "feat(tools): wire up a thing\n"
    "\n"
    "Body explaining the change.\n"
    "\n"
    "Co-Authored-By: Claude <noreply@anthropic.com>\n"
)

_COAUTHOR_MESSAGE_WITHOUT_TRAILER = (
    "feat(tools): wire up a thing\n"
    "\n"
    "Body explaining the change.\n"
    "\n"
    "Signed-off-by: Reviewer <r@example.com>\n"
)


def _run_trailer_check(message: str) -> list[str]:
    """Run the git-conventions trailer check using FORGE's own config."""
    cfg = load_config(REPO_ROOT)
    state_path = REPO_ROOT / ".forge" / "config.json"  # arbitrary anchor path
    compiled = _compile_bans(cfg.trailer_ban_patterns, state_path)
    assert compiled.compile_errors == (), (
        "FORGE's own ban patterns must compile cleanly; got "
        f"{[f.message for f in compiled.compile_errors]!r}"
    )
    _subject, trailers = _split_message(message)
    findings = _check_trailers(
        sha="abcdef0",
        message=message,
        trailers=trailers,
        compiled_bans=compiled,
        state_path=state_path,
    )
    return [f.message for f in findings]


def test_git_conventions_trailer_ban_fires_on_co_authored_by_claude() -> None:
    messages = _run_trailer_check(_COAUTHOR_MESSAGE_WITH_TRAILER)
    assert messages, "trailer-ban must fire on Co-Authored-By: Claude trailer"
    joined = " | ".join(messages)
    assert "Co-Authored-By" in joined or "co-authored-by" in joined.lower()


def test_git_conventions_trailer_ban_silent_on_clean_message() -> None:
    messages = _run_trailer_check(_COAUTHOR_MESSAGE_WITHOUT_TRAILER)
    assert messages == [], (
        f"clean message must not trip the trailer-ban; got findings: {messages!r}"
    )


# ---------------------------------------------------------------------------
# Sanity: the on-disk JSON is well-formed (defense-in-depth)
# ---------------------------------------------------------------------------


def test_conventions_json_is_wrapped_with_schema_version() -> None:
    payload = json.loads(CONVENTIONS_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert payload["schema_version"] == 1
    rules = payload["rules"]
    assert isinstance(rules, list)
    assert all(isinstance(entry, dict) for entry in rules)


def test_config_json_is_an_object_with_git_conventions_block() -> None:
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert "git_conventions" in payload

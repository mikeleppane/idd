"""Smoke test for AC #3 — Constitution gate ACKNOWLEDGE path end-to-end."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools import constitution as cn
from tools import ship_gate as sg
from tools.validate.state_semantic import validate_deviations

FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "feature-flag-full-constitution"
TARGET_REPO_REL = Path("target_repo")
FEATURE_ID = "2026-05-07-checkout-flow"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE_ROOT / TARGET_REPO_REL, dest)
    return dest


def test_gate_acknowledge_writes_full_audit_trail(repo: Path) -> None:
    feature = repo / ".idd" / "features" / FEATURE_ID
    review = feature / "REVIEW.code.md"
    state_path = feature / "state.json"
    decisions = feature / "decisions.md"

    articles, _dropped = cn.load_and_filter(repo, idea_text="checkout webhook")
    assert articles, "fixture must include CONSTITUTION.md with articles"

    findings = sg.parse_review_findings(review)
    gate, _warn, _info = sg.partition_by_article_level(findings, articles)
    assert gate, "fixture must surface ≥1 gating finding"
    assert all(f.article_id == "A3" for f in gate)

    prompt = sg.render_gate_prompt(gate, articles)
    assert "ACKNOWLEDGE" in prompt
    assert "[constitution:A3]" in prompt

    # Simulate user typing ACKNOWLEDGE: build the hook and invoke it the way
    # ship_feature would (post-preflight, pre-archive).
    ack_hook = sg.make_acknowledgement_hook(
        state_path=state_path,
        decisions_path=decisions,
        gate_findings=gate,
        articles=articles,
        now=datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
    )
    ack_hook(feature)

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    deviation = payload["deviations"][-1]
    assert deviation["phase"] == "ship"
    assert deviation["resolution"] == "user_acknowledged"
    assert "[constitution:A3]" in deviation["cause"]

    assert "Constitution finding acknowledged at ship" in decisions.read_text(encoding="utf-8")
    assert "[constitution:A3]" in decisions.read_text(encoding="utf-8")

    # Round-trip through validate_deviations so the audit-trail entry the gate
    # just wrote survives a future re-validate.
    assert validate_deviations(feature) == []


def test_ack_hook_is_idempotent_under_retry(repo: Path) -> None:
    """ship_feature retry semantics: hook may run again on a retry; the
    deviation must NOT be appended a second time."""
    feature = repo / ".idd" / "features" / FEATURE_ID
    state_path = feature / "state.json"
    decisions = feature / "decisions.md"

    articles, _ = cn.load_and_filter(repo, idea_text="checkout webhook")
    findings = sg.parse_review_findings(feature / "REVIEW.code.md")
    gate, _w, _i = sg.partition_by_article_level(findings, articles)

    hook = sg.make_acknowledgement_hook(
        state_path=state_path,
        decisions_path=decisions,
        gate_findings=gate,
        articles=articles,
        now=datetime(2026, 5, 7, 12, 0, 0, tzinfo=UTC),
    )
    hook(feature)
    hook(feature)
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(payload["deviations"]) == 1, "ack hook must be idempotent"


def test_gate_no_constitution_returns_empty(tmp_path: Path) -> None:
    repo_no_constitution = tmp_path / "repo_clean"
    repo_no_constitution.mkdir()
    articles, dropped = cn.load_and_filter(repo_no_constitution)
    assert articles == [] and dropped == []


def test_gate_no_findings_skips_prompt(repo: Path) -> None:
    feature = repo / ".idd" / "features" / FEATURE_ID
    review = feature / "REVIEW.code.md"
    review.write_text(
        (FIXTURE_ROOT.parents[0] / "_constitution" / "review_no_findings.md").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    articles, _ = cn.load_and_filter(repo)
    findings = sg.parse_review_findings(review)
    gate, warn, _info = sg.partition_by_article_level(findings, articles)
    assert gate == [] and warn == []
    assert sg.render_gate_prompt(gate, articles) == ""

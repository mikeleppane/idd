"""Tests for the ``forge-state`` Bash CLI surface (``tools.state_cli``).

The CLI is a thin shell over the six ``tools.state.*`` post-seed
mutators so agent callers can replace Python heredocs with mechanical
Bash subcommands. These tests pin down the subcommand dispatch matrix,
the ``--feature`` → state.json path resolution, the helper-refusal exit
code, and the argparse usage error path.

The state-writer hook refuses direct Write/Edit/MultiEdit against
state.json; this CLI is the canonical post-seed alternative. Tests
exercise each subcommand against a real seeded feature folder rather
than mocking the helpers so signature drift in
:mod:`tools.state` lights up here, not in a downstream dogfood session.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tools import state as state_mod
from tools.routing import seed_routed_feature
from tools.state import complete_phase, record_refined_idea, start_phase
from tools.state_cli import main


def _read_state(state_path: Path) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(state_path.read_text(encoding="utf-8"))
    return payload


def _seed_full(tmp_path: Path) -> tuple[str, Path]:
    """Seed a full-tier feature; return (feature_id, state.json path)."""
    folder = seed_routed_feature(
        tmp_path,
        idea="seeded idea",
        final_tier="full",
        proposed_tier="full",
        rationale="dogfood",
        constitution_present=False,
    )
    return folder.name, folder / "state.json"


def _seed_focused(tmp_path: Path) -> tuple[str, Path]:
    """Seed a focused-tier feature; return (feature_id, state.json path)."""
    folder = seed_routed_feature(
        tmp_path,
        idea="seeded idea",
        final_tier="focused",
        proposed_tier="focused",
        rationale="dogfood",
        constitution_present=False,
    )
    return folder.name, folder / "state.json"


def test_refine_subcommand_persists_idea(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``forge-state refine`` writes refined_idea on a full-tier feature."""
    monkeypatch.chdir(tmp_path)
    feature_id, state_path = _seed_full(tmp_path)

    rc = main(["refine", "--feature", feature_id, "--refined", "one-paragraph refined idea text"])

    assert rc == 0
    payload = _read_state(state_path)
    assert payload["refined_idea"] == "one-paragraph refined idea text"


def test_complete_phase_subcommand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``forge-state complete-phase`` marks the current phase done.

    Uses the full-tier refine phase because it has no semantic gate
    registered in :data:`tools.state._PHASE_GATES`; the test exercises
    CLI dispatch, not gate enforcement (which has its own coverage).
    """
    monkeypatch.chdir(tmp_path)
    feature_id, state_path = _seed_full(tmp_path)
    record_refined_idea(state_path, refined="refined idea")

    rc = main(["complete-phase", "--feature", feature_id, "--phase", "refine"])

    assert rc == 0
    payload = _read_state(state_path)
    assert payload["phases"]["refine"]["status"] == "done"


def test_start_phase_subcommand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``forge-state start-phase`` advances current_phase to the next slot."""
    monkeypatch.chdir(tmp_path)
    feature_id, state_path = _seed_full(tmp_path)
    record_refined_idea(state_path, refined="refined idea")
    complete_phase(state_path, "refine")

    rc = main(["start-phase", "--feature", feature_id, "--phase", "research"])

    assert rc == 0
    payload = _read_state(state_path)
    assert payload["current_phase"] == "research"
    assert payload["phases"]["research"]["status"] == "in_progress"


def test_set_current_slice_subcommand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``forge-state set-current-slice`` stamps the execute cursor.

    Advancing focused tier to execute crosses the spec semantic gate
    (SPEC.md scenarios + anchors), which the test fixture cannot satisfy
    without authoring real spec content. Monkeypatch the gate to no-op so
    the test stays focused on CLI dispatch; gate enforcement has dedicated
    coverage in test_state.py.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(state_mod, "_enforce_phase_gate", lambda *_a, **_k: None)
    feature_id, state_path = _seed_focused(tmp_path)
    complete_phase(state_path, "spec")
    start_phase(state_path, "execute")

    rc = main(["set-current-slice", "--feature", feature_id, "--slice", "2"])

    assert rc == 0
    payload = _read_state(state_path)
    assert payload["phases"]["execute"]["current_slice"] == 2


def test_record_commit_subcommand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``forge-state record-commit`` appends to state.commits[]."""
    monkeypatch.chdir(tmp_path)
    feature_id, state_path = _seed_focused(tmp_path)

    rc = main(
        [
            "record-commit",
            "--feature",
            feature_id,
            "--sha",
            "abc1234",
            "--phase",
            "spec",
            "--subject",
            "feat(spec): seed",
        ]
    )

    assert rc == 0
    payload = _read_state(state_path)
    assert len(payload["commits"]) == 1
    assert payload["commits"][0]["sha"] == "abc1234"
    assert payload["commits"][0]["subject"] == "feat(spec): seed"


def test_deviation_subcommand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``forge-state deviation`` appends to state.deviations[]."""
    monkeypatch.chdir(tmp_path)
    feature_id, state_path = _seed_focused(tmp_path)

    rc = main(
        [
            "deviation",
            "--feature",
            feature_id,
            "--phase",
            "spec",
            "--cause",
            "ambiguity in input",
            "--resolution",
            "narrowed scope",
        ]
    )

    assert rc == 0
    payload = _read_state(state_path)
    assert len(payload["deviations"]) == 1
    assert payload["deviations"][0]["cause"] == "ambiguity in input"
    assert payload["deviations"][0]["resolution"] == "narrowed scope"


def test_helper_refusal_returns_exit_1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """StateError from a helper surfaces as exit 1 with message on stderr."""
    monkeypatch.chdir(tmp_path)
    feature_id, _ = _seed_focused(tmp_path)

    # refine subcommand on focused tier: current_phase is 'spec', not 'refine'
    rc = main(["refine", "--feature", feature_id, "--refined", "bogus"])

    assert rc == 1
    captured = capsys.readouterr()
    assert "expected 'refine'" in captured.err


def test_repo_root_override(
    tmp_path: Path,
) -> None:
    """``--repo-root`` resolves state.json against the given root.

    Uses ``record-commit`` rather than ``complete-phase`` to keep the
    test gate-free; both subcommands share the same ``--repo-root``
    resolution path so the assertion stays meaningful.
    """
    feature_id, state_path = _seed_focused(tmp_path)

    rc = main(
        [
            "--repo-root",
            str(tmp_path),
            "record-commit",
            "--feature",
            feature_id,
            "--sha",
            "abc1234",
            "--phase",
            "spec",
            "--subject",
            "feat(spec): seed",
        ]
    )

    assert rc == 0
    payload = _read_state(state_path)
    assert len(payload["commits"]) == 1


def test_unknown_phase_choice_returns_exit_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """argparse refuses bogus --phase before any helper runs."""
    monkeypatch.chdir(tmp_path)
    feature_id, _ = _seed_focused(tmp_path)

    with pytest.raises(SystemExit) as exc:
        main(["complete-phase", "--feature", feature_id, "--phase", "bogus"])

    assert exc.value.code == 2


def test_missing_subcommand_returns_exit_2() -> None:
    """argparse refuses a bare invocation without a subcommand."""
    with pytest.raises(SystemExit) as exc:
        main([])

    assert exc.value.code == 2


def test_finish_subcommand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``forge-state finish`` sets current_phase='done' for focused tier."""
    monkeypatch.chdir(tmp_path)
    feature_id, state_path = _seed_focused(tmp_path)

    rc = main(["finish", "--feature", feature_id])

    assert rc == 0
    payload = _read_state(state_path)
    assert payload["current_phase"] == "done"


def test_complete_review_target_subcommand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``forge-state complete-review-target`` records a review target as done.

    Bypass `_enforce_phase_gate` because reaching review-phase from a
    fixture seed requires crossing the spec/scenarios/plan gates.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(state_mod, "_enforce_phase_gate", lambda *_a, **_k: None)
    feature_id, state_path = _seed_full(tmp_path)

    # Manually fast-forward through gate-protected phases to land at review/in_progress
    # with current_target='plan' so the CLI assertion is meaningful.
    record_refined_idea(state_path, refined="refined idea")
    for from_phase, to_phase in (
        ("refine", "research"),
        ("research", "spec"),
        ("spec", "domain"),
        ("domain", "scenarios"),
        ("scenarios", "plan"),
        ("plan", "crucible"),
        ("crucible", "review"),
    ):
        complete_phase(state_path, from_phase)
        start_phase(state_path, to_phase)
    payload = _read_state(state_path)
    payload["phases"]["review"]["current_target"] = "plan"
    state_path.write_text(json.dumps(payload), encoding="utf-8")

    rc = main(["complete-review-target", "--feature", feature_id, "--target", "plan"])

    assert rc == 0
    payload = _read_state(state_path)
    assert "plan" in payload["phases"]["review"]["targets_done"]

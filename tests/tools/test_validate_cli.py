"""Tests for tools.validate CLI dispatcher."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import validate


def test_health_clean_repo_returns_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = validate.main(["--target", "health", "--repo-root", str(tmp_path)])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload == {"target": "health", "findings": []}


def test_constitution_target_blocks_on_missing_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    constitution = tmp_path / "CONSTITUTION.md"

    rc = validate.main(["--target", "constitution", str(constitution)])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1
    assert any(f["severity"] == "BLOCK" for f in payload["findings"])


def test_unknown_target_exits_2(capsys: pytest.CaptureFixture[str]) -> None:
    rc = validate.main(["--target", "bogus"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "bogus" in captured.err


def test_all_target_runs_health_only_when_no_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = validate.main(["--target", "all", "--repo-root", str(tmp_path)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload["target"] == "all"
    assert payload["findings"] == []


def test_human_summary_on_stderr(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = validate.main(["--target", "health", "--repo-root", str(tmp_path)])
    captured = capsys.readouterr()
    assert rc == 0
    assert "validate" in captured.err.lower()


def test_per_file_target_without_path_returns_block(capsys: pytest.CaptureFixture[str]) -> None:
    """Spec/plan/delta require a positional path. Missing path is a BLOCK
    finding (exit 1), NOT a usage error (exit 2)."""
    for target in ("spec", "plan", "delta"):
        rc = validate.main(["--target", target])
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert rc == 1, f"target={target} should exit 1 on missing path"
        assert any(
            f["severity"] == "BLOCK" and "requires a path" in f["message"]
            for f in payload["findings"]
        ), payload


def test_high_severity_finding_triggers_exit_1(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """HIGH severity (e.g., capability collision) must drive non-zero exit."""
    feat_a = tmp_path / ".idd" / "features" / "2026-05-04-a"
    feat_b = tmp_path / ".idd" / "features" / "2026-05-04-b"
    for folder in (feat_a, feat_b):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "state.json").write_text(
            json.dumps(
                {
                    "feature_id": folder.name,
                    "tier": "focused",
                    "current_phase": "spec",
                    "phases": {"spec": {"status": "in_progress"}},
                    "skipped": [],
                    "deviations": [],
                    "commits": [],
                }
            ),
            encoding="utf-8",
        )
        (folder / "SPEC.md").write_text(
            f"---\nid: {folder.name}\nstatus: draft\ntier: focused\n"
            f"created: 2026-05-04\ncapability: shared\n---\n# Intent\nx.\n",
            encoding="utf-8",
        )

    rc = validate.main(["--target", "health", "--repo-root", str(tmp_path)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1
    assert any(f["severity"] == "HIGH" for f in payload["findings"])


def test_repo_wide_target_with_positional_path_emits_warn(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = validate.main(
        ["--target", "health", "--repo-root", str(tmp_path), str(tmp_path / "ignored.md")]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert any(
        f["severity"] == "WARN" and "ignores positional path" in f["message"]
        for f in payload["findings"]
    )


def test_repo_root_must_be_a_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--repo-root` pointing at a file (or any non-directory) must surface
    a BLOCK finding instead of silently returning 'no findings'. Otherwise
    the user thinks the repo is healthy when actually the path was wrong."""
    not_a_dir = tmp_path / "this-is-a-file.txt"
    not_a_dir.write_text("not a repo", encoding="utf-8")

    rc = validate.main(["--target", "health", "--repo-root", str(not_a_dir)])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1
    assert any(
        f["severity"] == "BLOCK" and "repo-root" in f["message"].lower()
        for f in payload["findings"]
    ), payload

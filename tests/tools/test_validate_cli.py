"""Tests for tools.validate CLI dispatcher."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import validate
from tools.validate import cli as validate_cli


def test_health_clean_repo_returns_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An empty ``.forge/`` directory is a healthy, bootstrapped repo:
    the missing-dir WARN must not fire and the CLI must report a clean
    findings list with exit 0."""
    (tmp_path / ".forge").mkdir()

    rc = validate.main(["--target", "health", "--repo-root", str(tmp_path)])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload == {"target": "health", "findings": []}


def test_validate_cli_exit_code_when_only_warn_findings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Running health against a directory with no ``.forge/`` emits a WARN
    (no .forge/), but WARN must not flip the exit code: BLOCK/HIGH ⇒ 1,
    everything else ⇒ 0. The user still sees the warning in the JSON payload."""
    rc = validate.main(["--target", "health", "--repo-root", str(tmp_path)])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    warns = [f for f in payload["findings"] if f["severity"] == "WARN"]
    assert any(".forge" in f["message"] for f in warns), payload


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
    """A bootstrapped (empty .forge/) directory under ``--target all`` runs
    health and the no-path-required validators with no findings and exit 0."""
    (tmp_path / ".forge").mkdir()

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
    feat_a = tmp_path / ".forge" / "features" / "2026-05-04-a"
    feat_b = tmp_path / ".forge" / "features" / "2026-05-04-b"
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


def test_cli_accepts_new_targets(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Every new P2b --target value is accepted (exit 0 or 1, never 2)."""
    spec = tmp_path / "SPEC.md"
    spec.write_text(
        "# Scenarios\nScenario: 1 demo\n# Acceptance Criteria\n1. crit-1 done\n",
        encoding="utf-8",
    )
    plan = tmp_path / "PLAN.md"
    plan.write_text(
        "# Slice 1: x\n**Files in scope:** a.py\n**Acceptance:** crit-1\n",
        encoding="utf-8",
    )
    feature = tmp_path
    (feature / "state.json").write_text('{"deviations": []}', encoding="utf-8")

    cases: list[tuple[str, Path]] = [
        ("scenarios", spec),
        ("anchors", spec),
        ("spec-semantic", spec),
        ("plan-tasks", plan),
        ("verified-deps", plan),
        ("deviations", feature),
    ]
    for tgt, p in cases:
        rc = validate.main(["--target", tgt, "--repo-root", str(tmp_path), str(p)])
        capsys.readouterr()
        assert rc in (0, 1), f"target {tgt} returned {rc}"


def test_cli_target_all_fans_out_over_features(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--target all` must aggregate per-feature semantic validators in addition
    to repo-wide health/ship calls. Patches are applied to the CLI module's
    own bindings (cli.py imports siblings — package-level re-export is a
    different binding object)."""
    sentinel_calls: list[str] = []

    def make_sentinel(name: str) -> object:
        def fn(*args: object, **kwargs: object) -> list[object]:
            sentinel_calls.append(name)
            return []

        return fn

    monkeypatch.setattr(validate_cli, "validate_health", make_sentinel("health"))
    monkeypatch.setattr(validate_cli, "validate_capability_uniqueness", make_sentinel("ship"))
    monkeypatch.setattr(validate_cli, "validate_deviations", make_sentinel("deviations"))
    monkeypatch.setattr(validate_cli, "validate_scenarios", make_sentinel("scenarios"))
    monkeypatch.setattr(validate_cli, "validate_anchors", make_sentinel("anchors"))
    monkeypatch.setattr(validate_cli, "validate_negative_requirements", make_sentinel("nr"))
    monkeypatch.setattr(validate_cli, "validate_frontmatter", make_sentinel("fm"))

    feat = tmp_path / ".forge" / "features" / "2026-05-05-x"
    feat.mkdir(parents=True)
    (feat / "state.json").write_text('{"deviations": []}', encoding="utf-8")
    (feat / "SPEC.md").write_text("# Scenarios\n# Acceptance Criteria\n", encoding="utf-8")

    rc = validate.main(["--target", "all", "--repo-root", str(tmp_path)])
    capsys.readouterr()
    assert rc in (0, 1)
    assert "health" in sentinel_calls
    assert "ship" in sentinel_calls
    assert "deviations" in sentinel_calls
    assert "scenarios" in sentinel_calls
    assert "anchors" in sentinel_calls


def test_cli_target_all_skips_archive_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--target all` must not iterate the `archive/` reserved sub-folder.

    Archived features live at ``.forge/features/archive/<id>/`` and must not be
    treated as live features by the dispatcher — otherwise the dispatcher
    invokes per-feature validators with ``feature_id="archive"``, surfacing
    spurious findings (or downstream crashes) for a folder that contains no
    state.json of its own.
    """
    seen_feature_ids: list[str] = []

    def capture_feature(*args: object, **kwargs: object) -> list[object]:
        # validate_deviations is invoked with the feature folder; capture name.
        if args and isinstance(args[0], Path):
            seen_feature_ids.append(args[0].name)
        return []

    def noop(*_args: object, **_kwargs: object) -> list[object]:
        return []

    monkeypatch.setattr(validate_cli, "validate_deviations", capture_feature)
    monkeypatch.setattr(validate_cli, "validate_tdd_evidence", noop)
    monkeypatch.setattr(validate_cli, "validate_domain_glossary", noop)
    monkeypatch.setattr(validate_cli, "validate_qa_shape", noop)

    archived = tmp_path / ".forge" / "features" / "archive" / "2026-05-01-old"
    archived.mkdir(parents=True)
    (archived / "state.json").write_text('{"deviations": []}', encoding="utf-8")

    live = tmp_path / ".forge" / "features" / "2026-05-09-live"
    live.mkdir(parents=True)
    (live / "state.json").write_text('{"deviations": []}', encoding="utf-8")

    rc = validate.main(["--target", "all", "--repo-root", str(tmp_path)])
    capsys.readouterr()
    assert rc in (0, 1)

    assert "archive" not in seen_feature_ids, (
        f"dispatcher walked into archive/ sub-folder: {seen_feature_ids}"
    )
    assert "2026-05-09-live" in seen_feature_ids, seen_feature_ids


def test_cli_check_registries_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--check-registries` forwards to validate_verified_deps."""
    captured: dict[str, bool] = {}

    def fake(plan_path: Path, *, check_registries: bool) -> list[object]:
        captured["check"] = check_registries
        return []

    monkeypatch.setattr(validate_cli, "validate_verified_deps", fake)
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Slice 1: x\n**Acceptance:** crit-1\n", encoding="utf-8")
    rc = validate.main(["--target", "verified-deps", "--check-registries", str(plan)])
    capsys.readouterr()
    assert rc in (0, 1)
    assert captured["check"] is True


def test_cli_check_registries_default_false(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without --check-registries, the flag is False by default."""
    captured: dict[str, bool] = {}

    def fake(plan_path: Path, *, check_registries: bool) -> list[object]:
        captured["check"] = check_registries
        return []

    monkeypatch.setattr(validate_cli, "validate_verified_deps", fake)
    plan = tmp_path / "PLAN.md"
    plan.write_text("# Slice 1: x\n**Acceptance:** crit-1\n", encoding="utf-8")
    rc = validate.main(["--target", "verified-deps", str(plan)])
    capsys.readouterr()
    assert rc in (0, 1)
    assert captured["check"] is False


def test_cli_deviations_requires_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--target deviations` BLOCKs when positional path is not a directory."""
    f = tmp_path / "not_a_dir.txt"
    f.write_text("x", encoding="utf-8")
    rc = validate.main(["--target", "deviations", str(f)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1
    assert any(
        f["severity"] == "BLOCK" and "directory" in f["message"].lower()
        for f in payload["findings"]
    ), payload


def test_cli_deviations_requires_path(capsys: pytest.CaptureFixture[str]) -> None:
    """`--target deviations` without a positional path BLOCKs."""
    rc = validate.main(["--target", "deviations"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1
    assert any(
        f["severity"] == "BLOCK" and "folder" in f["message"].lower() for f in payload["findings"]
    ), payload


def test_cli_renders_fix_hint_when_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When a validator emits a Finding with fix_hint, the CLI JSON payload
    renders the hint under the ``fix_hint`` key. Findings without a hint
    keep the dict shape stable (no `fix_hint` key)."""

    def fake_health(repo_root: Path) -> list[validate.Finding]:
        return [
            validate.Finding(
                severity="BLOCK",
                target="health",
                file=repo_root / "made-up.md",
                message="synthetic block for fix_hint rendering",
                fix_hint="Run `forge fix synthetic`.",
            ),
            validate.Finding(
                severity="WARN",
                target="health",
                file=repo_root / "advisory.md",
                message="advisory without recovery action",
            ),
        ]

    monkeypatch.setattr(validate_cli, "validate_health", fake_health)

    rc = validate.main(["--target", "health", "--repo-root", str(tmp_path)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert rc == 1
    block = next(f for f in payload["findings"] if f["severity"] == "BLOCK")
    assert block["fix_hint"] == "Run `forge fix synthetic`."
    advisory = next(f for f in payload["findings"] if f["severity"] == "WARN")
    assert "fix_hint" not in advisory


def test_cli_target_all_surfaces_malformed_lessons_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A malformed ``.forge/intel/lessons.md`` must BLOCK under ``--target all``.

    Earlier revisions of the all-target dispatcher walked the Constitution and
    conventions but skipped ``validate_lessons``, leaving a broken trap-memory
    artifact invisible to repo-wide validation while the per-target
    ``--target lessons`` path correctly reported it.
    """
    lessons = tmp_path / ".forge" / "intel" / "lessons.md"
    lessons.parent.mkdir(parents=True)
    # Missing required Captured field — parser raises LessonError, which the
    # validator surfaces as a single BLOCK finding.
    lessons.write_text(
        "## L001 — broken\n"
        "**Resolved by:** manual\n"
        "**Trap:** t\n"
        "**Avoidance:** a\n"
        "**Tags:** dispatch\n"
        "**Severity:** LOW\n"
        "**Status:** active\n",
        encoding="utf-8",
    )

    rc = validate.main(["--target", "all", "--repo-root", str(tmp_path)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert rc == 1
    block = next(
        (f for f in payload["findings"] if f["severity"] == "BLOCK" and f["target"] == "lessons"),
        None,
    )
    assert block is not None, payload

"""Tests for validate_git_conventions (commit message structural validator)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tools.validate import Finding
from tools.validate.cli import main as cli_main
from tools.validate.git_conventions import (
    load_config,
    validate_git_conventions,
)

# --- Helpers ------------------------------------------------------------------


def _feature_layout(tmp_path: Path, feature_id: str = "2026-05-11-fx") -> Path:
    """Build a feature folder under ``tmp_path/.forge/features/<id>/`` and return it."""
    folder = tmp_path / ".forge" / "features" / feature_id
    folder.mkdir(parents=True)
    return folder


def _write_state(folder: Path, commits: list[dict[str, Any]] | None) -> None:
    payload: dict[str, Any] = {
        "feature_id": folder.name,
        "tier": "focused",
        "current_phase": "refine",
        "phases": {},
        "skipped": [],
        "deviations": [],
        "commits": commits if commits is not None else [],
    }
    (folder / "state.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_config(repo_root: Path, payload: dict[str, Any]) -> None:
    forge = repo_root / ".forge"
    forge.mkdir(exist_ok=True)
    (forge / "config.json").write_text(json.dumps(payload), encoding="utf-8")


class _ScriptedRunner:
    """Test seam that returns canned ``CompletedProcess`` results per ``git`` argv."""

    def __init__(self, scripts: dict[tuple[str, ...], Any]) -> None:
        # value is either a CompletedProcess factory args tuple OR a callable raising
        self._scripts = scripts
        self.calls: list[tuple[str, ...]] = []

    def __call__(
        self,
        args: list[str],
        *,
        capture_output: bool = False,
        text: bool = False,
        check: bool = False,
        timeout: float | None = None,
        cwd: Path | None = None,
        **_extra: Any,
    ) -> subprocess.CompletedProcess[str]:
        key = tuple(args)
        self.calls.append(key)
        if key not in self._scripts:
            raise AssertionError(f"unscripted git invocation: {key}")
        scripted = self._scripts[key]
        if callable(scripted):
            return scripted()  # type: ignore[no-any-return]
        returncode, stdout, stderr = scripted
        return subprocess.CompletedProcess(
            args=args, returncode=returncode, stdout=stdout, stderr=stderr
        )


def _script_commit(sha: str, message: str) -> dict[tuple[str, ...], Any]:
    return {
        ("git", "rev-parse", "--verify", f"{sha}^{{commit}}"): (0, sha + "\n", ""),
        ("git", "show", "-s", "--format=%B", sha): (0, message, ""),
    }


def _script_unknown_sha(sha: str) -> dict[tuple[str, ...], Any]:
    return {
        ("git", "rev-parse", "--verify", f"{sha}^{{commit}}"): (
            128,
            "",
            "fatal: bad revision\n",
        ),
    }


# --- load_config --------------------------------------------------------------


def test_load_config_missing_file_returns_defaults(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    assert config.subject_max_length == 72
    assert config.require_conventional_commits is True
    assert config.allowed_scopes == ()
    assert config.trailer_ban_patterns == ()


def test_load_config_without_git_conventions_block_returns_defaults(tmp_path: Path) -> None:
    _write_config(tmp_path, {"cross_ai": {"mode": "manual"}})
    config = load_config(tmp_path)
    assert config.subject_max_length == 72
    assert config.require_conventional_commits is True
    assert config.allowed_scopes == ()
    assert config.trailer_ban_patterns == ()


def test_load_config_partial_subject_only_max_length(tmp_path: Path) -> None:
    _write_config(tmp_path, {"git_conventions": {"subject": {"max_length": 50}}})
    config = load_config(tmp_path)
    assert config.subject_max_length == 50
    # Other defaults must still apply.
    assert config.require_conventional_commits is True
    assert config.allowed_scopes == ()


def test_load_config_full_block(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        {
            "git_conventions": {
                "subject": {
                    "max_length": 80,
                    "require_conventional_commits": False,
                    "allowed_scopes": ["tools", "skills"],
                },
                "trailers": {"ban_patterns": ["Co-Authored-By: Claude.*"]},
            }
        },
    )
    config = load_config(tmp_path)
    assert config.subject_max_length == 80
    assert config.require_conventional_commits is False
    assert config.allowed_scopes == ("tools", "skills")
    assert config.trailer_ban_patterns == ("Co-Authored-By: Claude.*",)


def test_load_config_malformed_json_falls_back_to_defaults(tmp_path: Path) -> None:
    forge = tmp_path / ".forge"
    forge.mkdir()
    (forge / "config.json").write_text("{not json", encoding="utf-8")
    config = load_config(tmp_path)
    assert config.subject_max_length == 72
    assert config.require_conventional_commits is True


def test_load_config_returns_frozen_dataclass(tmp_path: Path) -> None:
    config = load_config(tmp_path)
    with pytest.raises(AttributeError):
        config.subject_max_length = 50  # type: ignore[misc]


# --- State loading ------------------------------------------------------------


def test_missing_state_json_emits_block(tmp_path: Path) -> None:
    folder = tmp_path / ".forge" / "features" / "2026-05-11-fx"
    folder.mkdir(parents=True)
    findings = validate_git_conventions(folder, runner=_ScriptedRunner({}))
    assert len(findings) == 1
    assert findings[0].severity == "BLOCK"
    assert "state.json" in str(findings[0].file)


def test_malformed_state_json_emits_block(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    (folder / "state.json").write_text("{not json", encoding="utf-8")
    findings = validate_git_conventions(folder, runner=_ScriptedRunner({}))
    assert len(findings) == 1
    assert findings[0].severity == "BLOCK"


def test_no_commits_field_returns_no_findings(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    (folder / "state.json").write_text(json.dumps({"feature_id": folder.name}), encoding="utf-8")
    findings = validate_git_conventions(folder, runner=_ScriptedRunner({}))
    assert findings == []


def test_empty_commits_list_returns_no_findings(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    _write_state(folder, [])
    findings = validate_git_conventions(folder, runner=_ScriptedRunner({}))
    assert findings == []


def test_commits_field_not_a_list_returns_no_findings(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    (folder / "state.json").write_text(
        json.dumps({"feature_id": folder.name, "commits": "oops"}),
        encoding="utf-8",
    )
    findings = validate_git_conventions(folder, runner=_ScriptedRunner({}))
    assert findings == []


# --- Happy path --------------------------------------------------------------


def test_single_valid_commit_no_findings(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    sha = "a" * 40
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": "feat(tools): add x"}])
    scripts = _script_commit(sha, "feat(tools): add x\n")
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    assert findings == []


def test_multiple_valid_commits_no_findings(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    sha1 = "a" * 40
    sha2 = "b" * 40
    _write_state(
        folder,
        [
            {"sha": sha1, "phase": "spec", "subject": "feat(tools): one"},
            {"sha": sha2, "phase": "plan", "subject": "fix(skills): two"},
        ],
    )
    scripts: dict[tuple[str, ...], Any] = {}
    scripts.update(_script_commit(sha1, "feat(tools): one\n"))
    scripts.update(_script_commit(sha2, "fix(skills): two\n"))
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    assert findings == []


# --- Subject length checks ----------------------------------------------------


def test_subject_exactly_72_chars_passes(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    sha = "a" * 40
    subject = "feat(tools): " + "x" * (72 - len("feat(tools): "))
    assert len(subject) == 72
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, subject + "\n")
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    assert findings == []


def test_subject_73_chars_emits_high(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    sha = "a" * 40
    subject = "feat(tools): " + "x" * (73 - len("feat(tools): "))
    assert len(subject) == 73
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, subject + "\n")
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    assert any(f.severity == "HIGH" and "subject exceeds 72" in f.message for f in findings), (
        findings
    )


def test_subject_length_uses_config_override(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    _write_config(tmp_path, {"git_conventions": {"subject": {"max_length": 50}}})
    sha = "a" * 40
    subject = "feat(tools): " + "x" * (51 - len("feat(tools): "))
    assert len(subject) == 51
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, subject + "\n")
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    assert any(f.severity == "HIGH" and "subject exceeds 50" in f.message for f in findings), (
        findings
    )


# --- Conventional Commits grammar --------------------------------------------


def test_subject_without_type_emits_high(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    sha = "a" * 40
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": "update stuff"}])
    scripts = _script_commit(sha, "update stuff\n")
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    assert any(f.severity == "HIGH" and "Conventional Commits" in f.message for f in findings), (
        findings
    )


def test_subject_with_no_scope_allowed_when_scope_filter_empty(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    sha = "a" * 40
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": "feat: add thing"}])
    scripts = _script_commit(sha, "feat: add thing\n")
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    assert findings == []


def test_subject_scope_not_in_allowed_emits_high(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    _write_config(
        tmp_path,
        {"git_conventions": {"subject": {"allowed_scopes": ["tools", "skills"]}}},
    )
    sha = "a" * 40
    subject = "feat(weird-scope): add thing"
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, subject + "\n")
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    assert any(
        f.severity == "HIGH" and "weird-scope" in f.message and "allowed_scopes" in f.message
        for f in findings
    ), findings


def test_subject_scope_in_allowed_passes(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    _write_config(
        tmp_path,
        {"git_conventions": {"subject": {"allowed_scopes": ["tools", "skills"]}}},
    )
    sha = "a" * 40
    subject = "feat(tools): add thing"
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, subject + "\n")
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    assert findings == []


def test_subject_missing_scope_allowed_even_with_scope_filter(tmp_path: Path) -> None:
    """Missing scope is allowed when allowed_scopes is set — some types omit it."""
    folder = _feature_layout(tmp_path)
    _write_config(
        tmp_path,
        {"git_conventions": {"subject": {"allowed_scopes": ["tools", "skills"]}}},
    )
    sha = "a" * 40
    subject = "feat: add thing"
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, subject + "\n")
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    assert findings == []


def test_subject_wip_prefix_rejected(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    sha = "a" * 40
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": "WIP: something"}])
    scripts = _script_commit(sha, "WIP: something\n")
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    assert any(f.severity == "HIGH" and "Conventional Commits" in f.message for f in findings), (
        findings
    )


def test_require_conventional_commits_off_accepts_any_subject(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    _write_config(
        tmp_path,
        {"git_conventions": {"subject": {"require_conventional_commits": False}}},
    )
    sha = "a" * 40
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": "whatever"}])
    scripts = _script_commit(sha, "whatever\n")
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    assert findings == []


def test_subject_breaking_bang_marker_accepted(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    sha = "a" * 40
    subject = "feat(tools)!: breaking change"
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, subject + "\n")
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    assert findings == []


def test_merge_commit_rejected_when_conventional_required(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    sha = "a" * 40
    subject = "Merge branch 'main' into feat/x"
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, subject + "\n")
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    assert any(f.severity == "HIGH" and "Conventional Commits" in f.message for f in findings), (
        findings
    )


# --- Trailer bans -------------------------------------------------------------


def test_banned_trailer_in_trailer_block_emits_block(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    _write_config(
        tmp_path,
        {"git_conventions": {"trailers": {"ban_patterns": ["Co-Authored-By: Claude.*"]}}},
    )
    sha = "a" * 40
    subject = "feat(tools): add x"
    body = (
        f"{subject}\n"
        "\n"
        "Some body explaining the change.\n"
        "\n"
        "Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>\n"
    )
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, body)
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    blockers = [f for f in findings if f.severity == "BLOCK"]
    assert len(blockers) == 1
    assert "Co-Authored-By: Claude" in blockers[0].message


def test_banned_pattern_in_body_only_not_flagged(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    _write_config(
        tmp_path,
        {"git_conventions": {"trailers": {"ban_patterns": ["Co-Authored-By: Claude.*"]}}},
    )
    sha = "a" * 40
    subject = "feat(tools): add x"
    body = (
        f"{subject}\n"
        "\n"
        "Body discussing Co-Authored-By: Claude as a hypothetical example.\n"
        "\n"
        "Signed-off-by: Mikko <mikko@example.com>\n"
    )
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, body)
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    assert all(f.severity != "BLOCK" for f in findings), findings


def test_empty_ban_patterns_never_flags_trailers(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    sha = "a" * 40
    subject = "feat(tools): add x"
    body = f"{subject}\n\nBody.\n\nCo-Authored-By: Claude Opus <noreply@anthropic.com>\n"
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, body)
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    # No trailer ban configured -> no BLOCK findings tied to trailers.
    assert all(f.severity != "BLOCK" for f in findings), findings


def test_multiple_ban_patterns_one_hit(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    _write_config(
        tmp_path,
        {
            "git_conventions": {
                "trailers": {
                    "ban_patterns": [
                        "Co-Authored-By: Claude.*",
                        "Forbidden-Trailer: .*",
                    ]
                }
            }
        },
    )
    sha = "a" * 40
    subject = "feat(tools): add x"
    body = f"{subject}\n\nBody.\n\nCo-Authored-By: Claude Opus <noreply@anthropic.com>\n"
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, body)
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    blockers = [f for f in findings if f.severity == "BLOCK"]
    assert len(blockers) == 1


def test_signed_off_by_trailer_parses_with_hyphens(tmp_path: Path) -> None:
    """Hyphenated trailer keys (RFC 5322 style) parse into the trailer block."""
    folder = _feature_layout(tmp_path)
    _write_config(
        tmp_path,
        {"git_conventions": {"trailers": {"ban_patterns": ["Signed-off-by: .*"]}}},
    )
    sha = "a" * 40
    subject = "feat(tools): add x"
    body = f"{subject}\n\nBody.\n\nSigned-off-by: Someone <s@example.com>\n"
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, body)
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    blockers = [f for f in findings if f.severity == "BLOCK"]
    assert len(blockers) == 1


# --- Missing SHA / runner errors ---------------------------------------------


def test_rev_parse_failure_emits_warn(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    sha = "a" * 40
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": "feat(tools): add"}])
    runner = _ScriptedRunner(_script_unknown_sha(sha))
    findings = validate_git_conventions(folder, runner=runner)
    assert len(findings) == 1
    assert findings[0].severity == "WARN"
    assert f"unknown-sha:{sha}" in findings[0].message


def test_show_failure_after_rev_parse_passes_emits_warn(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    sha = "a" * 40
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": "feat(tools): add"}])
    scripts: dict[tuple[str, ...], Any] = {
        ("git", "rev-parse", "--verify", f"{sha}^{{commit}}"): (0, sha + "\n", ""),
        ("git", "show", "-s", "--format=%B", sha): (128, "", "fatal: bad object\n"),
    }
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    assert len(findings) == 1
    assert findings[0].severity == "WARN"
    assert f"unknown-sha:{sha}" in findings[0].message


def test_subprocess_timeout_emits_warn(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    sha = "a" * 40
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": "feat(tools): add"}])

    def _raise_timeout() -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["git"], timeout=10)

    runner = _ScriptedRunner(
        {("git", "rev-parse", "--verify", f"{sha}^{{commit}}"): _raise_timeout}
    )
    findings = validate_git_conventions(folder, runner=runner)
    assert len(findings) == 1
    assert findings[0].severity == "WARN"
    assert f"unknown-sha:{sha}" in findings[0].message


def test_filenotfound_emits_warn(tmp_path: Path) -> None:
    """No git binary on PATH -> WARN, never raise."""
    folder = _feature_layout(tmp_path)
    sha = "a" * 40
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": "feat(tools): add"}])

    def _raise_missing() -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(2, "No such file", "git")

    runner = _ScriptedRunner(
        {("git", "rev-parse", "--verify", f"{sha}^{{commit}}"): _raise_missing}
    )
    findings = validate_git_conventions(folder, runner=runner)
    assert len(findings) == 1
    assert findings[0].severity == "WARN"
    assert f"unknown-sha:{sha}" in findings[0].message


def test_unknown_sha_skips_further_checks(tmp_path: Path) -> None:
    """When rev-parse fails, no subject/trailer checks run for that row."""
    folder = _feature_layout(tmp_path)
    sha = "a" * 40
    # Subject is bad (no type) — would normally produce a HIGH finding.
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": "broken subject"}])
    runner = _ScriptedRunner(_script_unknown_sha(sha))
    findings = validate_git_conventions(folder, runner=runner)
    assert len(findings) == 1
    assert findings[0].severity == "WARN"


# --- Trailer parsing edge cases ----------------------------------------------


def test_keyvalue_in_body_not_parsed_as_trailer(tmp_path: Path) -> None:
    """Only the trailing contiguous Key: value block counts as trailers."""
    folder = _feature_layout(tmp_path)
    _write_config(
        tmp_path,
        {"git_conventions": {"trailers": {"ban_patterns": ["Forbidden: .*"]}}},
    )
    sha = "a" * 40
    subject = "feat(tools): add x"
    body = (
        f"{subject}\n"
        "\n"
        "Forbidden: this is in the body, mid-paragraph.\n"
        "More body text follows.\n"
        "\n"
        "Signed-off-by: Someone <s@example.com>\n"
    )
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, body)
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    # The "Forbidden:" line is in the body block, not the trailing trailer block,
    # so it must NOT match the trailer ban.
    assert all(f.severity != "BLOCK" for f in findings), findings


def test_only_subject_no_body_no_trailers(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    _write_config(
        tmp_path,
        {"git_conventions": {"trailers": {"ban_patterns": ["Anything: .*"]}}},
    )
    sha = "a" * 40
    subject = "feat(tools): add x"
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, subject + "\n")
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    assert findings == []


# --- Determinism --------------------------------------------------------------


def test_repeated_invocations_produce_identical_findings(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    _write_config(
        tmp_path,
        {"git_conventions": {"trailers": {"ban_patterns": ["Co-Authored-By: Claude.*"]}}},
    )
    sha1 = "a" * 40
    sha2 = "b" * 40
    _write_state(
        folder,
        [
            {"sha": sha1, "phase": "spec", "subject": "bad subject"},
            {"sha": sha2, "phase": "plan", "subject": "feat(tools): ok"},
        ],
    )
    scripts: dict[tuple[str, ...], Any] = {}
    scripts.update(_script_commit(sha1, "bad subject\n"))
    scripts.update(
        _script_commit(
            sha2,
            "feat(tools): ok\n\nBody.\n\nCo-Authored-By: Claude X <a@b.com>\n",
        )
    )
    one = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    two = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    assert [(f.severity, f.message, str(f.file)) for f in one] == [
        (f.severity, f.message, str(f.file)) for f in two
    ]


# --- Finding file path field --------------------------------------------------


def test_finding_path_points_at_state_json(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    sha = "a" * 40
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": "no-type"}])
    scripts = _script_commit(sha, "no-type\n")
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    assert findings
    for finding in findings:
        assert finding.file.name == "state.json"


# --- Default runner -----------------------------------------------------------


def test_default_runner_is_used_when_none_passed(tmp_path: Path) -> None:
    """When runner is omitted the validator must call subprocess.run with safe defaults."""
    folder = _feature_layout(tmp_path)
    # No commits -> no git calls; validator must not raise.
    _write_state(folder, [])
    findings = validate_git_conventions(folder)
    assert findings == []


# --- Result type --------------------------------------------------------------


def test_returns_list_of_findings(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    _write_state(folder, [])
    findings = validate_git_conventions(folder, runner=_ScriptedRunner({}))
    assert isinstance(findings, list)
    for f in findings:
        assert isinstance(f, Finding)


# --- CLI integration ---------------------------------------------------------


def test_cli_target_git_conventions_runs_clean(tmp_path: Path) -> None:
    """`python -m tools.validate --target git-conventions <folder>` exits 0 with no findings."""
    folder = _feature_layout(tmp_path)
    _write_state(folder, [])
    rc = cli_main(
        [
            "--target",
            "git-conventions",
            "--repo-root",
            str(tmp_path),
            str(folder),
        ]
    )
    assert rc == 0


def test_cli_target_git_conventions_requires_folder(tmp_path: Path) -> None:
    rc = cli_main(
        [
            "--target",
            "git-conventions",
            "--repo-root",
            str(tmp_path),
        ]
    )
    # Missing folder path -> BLOCK finding -> non-zero exit.
    assert rc == 1


# --- Type guard on runner -----------------------------------------------------


def test_runner_callable_protocol_accepts_subprocess_run() -> None:
    """The runner type matches subprocess.run so callers can pass it directly."""
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
    assert callable(runner)

"""Trailer-block robustness and case-insensitive ban-pattern matching."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from tools.validate.git_conventions import validate_git_conventions

# --- Helpers (DAMP copies — kept local so each test reads as a spec) ---------


def _feature_layout(tmp_path: Path, feature_id: str = "2026-05-11-fx") -> Path:
    folder = tmp_path / ".forge" / "features" / feature_id
    folder.mkdir(parents=True)
    return folder


def _write_state(folder: Path, commits: list[dict[str, Any]]) -> None:
    payload: dict[str, Any] = {
        "feature_id": folder.name,
        "tier": "focused",
        "current_phase": "refine",
        "phases": {},
        "skipped": [],
        "deviations": [],
        "commits": commits,
    }
    (folder / "state.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_config(repo_root: Path, payload: dict[str, Any]) -> None:
    forge = repo_root / ".forge"
    forge.mkdir(exist_ok=True)
    (forge / "config.json").write_text(json.dumps(payload), encoding="utf-8")


class _ScriptedRunner:
    def __init__(self, scripts: dict[tuple[str, ...], Any]) -> None:
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


# --- Malformed trailer-block fallback (silent-bypass closure) ----------------


def test_banned_trailer_fires_when_block_has_non_trailer_tail_line(tmp_path: Path) -> None:
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
        "body line\n"
        "\n"
        "Co-Authored-By: Claude <x@anthropic.com>\n"
        "not-a-trailer-shaped line\n"
    )
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, body)
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    blockers = [f for f in findings if f.severity == "BLOCK"]
    assert len(blockers) >= 1, findings
    assert any("Co-Authored-By: Claude" in f.message for f in blockers), blockers


def test_proper_trailer_block_with_banned_line_fires_exactly_once(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    _write_config(
        tmp_path,
        {"git_conventions": {"trailers": {"ban_patterns": ["Co-Authored-By: Claude.*"]}}},
    )
    sha = "a" * 40
    subject = "feat(tools): add x"
    body = f"{subject}\n\nBody.\n\nCo-Authored-By: Claude Opus <noreply@anthropic.com>\n"
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, body)
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    blockers = [f for f in findings if f.severity == "BLOCK"]
    assert len(blockers) == 1, blockers


def test_in_prose_trailer_shape_without_banned_body_is_silent(tmp_path: Path) -> None:
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
        "Here is some prose: Co-Authored-By: Bob (this is body, not a trailer)\n"
        "\n"
        "Real-Trailer: ok\n"
    )
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, body)
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    assert all(f.severity != "BLOCK" for f in findings), findings


def test_banned_trailer_text_earlier_in_message_is_caught_by_fallback(tmp_path: Path) -> None:
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
        "Co-Authored-By: Claude <leaked@anthropic.com>\n"
        "This is body content, not a trailer block.\n"
        "\n"
        "Signed-off-by: someone <s@example.com>\n"
    )
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, body)
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    blockers = [f for f in findings if f.severity == "BLOCK"]
    assert len(blockers) >= 1, findings
    assert any("Co-Authored-By: Claude" in f.message for f in blockers), blockers


# --- Case-insensitive ban-pattern matching -----------------------------------


def test_lowercase_trailer_matches_mixed_case_ban_pattern(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    _write_config(
        tmp_path,
        {"git_conventions": {"trailers": {"ban_patterns": ["Co-Authored-By: Claude.*"]}}},
    )
    sha = "a" * 40
    subject = "feat(tools): add x"
    body = f"{subject}\n\nBody.\n\nco-authored-by: claude <x@anthropic.com>\n"
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, body)
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    blockers = [f for f in findings if f.severity == "BLOCK"]
    assert len(blockers) >= 1, findings


def test_different_body_does_not_match_even_case_insensitively(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    _write_config(
        tmp_path,
        {"git_conventions": {"trailers": {"ban_patterns": ["Co-Authored-By: Claude.*"]}}},
    )
    sha = "a" * 40
    subject = "feat(tools): add x"
    body = f"{subject}\n\nBody.\n\nCo-Authored-By: Anthropic <y@anthropic.com>\n"
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, body)
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    assert all(f.severity != "BLOCK" for f in findings), findings


def test_inline_ignorecase_flag_in_pattern_works_alongside_default(tmp_path: Path) -> None:
    folder = _feature_layout(tmp_path)
    _write_config(
        tmp_path,
        {"git_conventions": {"trailers": {"ban_patterns": ["(?i)co-authored-by: claude.*"]}}},
    )
    sha = "a" * 40
    subject = "feat(tools): add x"
    body = f"{subject}\n\nBody.\n\nCo-Authored-By: Claude Mixed <z@anthropic.com>\n"
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, body)
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    blockers = [f for f in findings if f.severity == "BLOCK"]
    assert len(blockers) >= 1, findings


# --- Multiple patterns -------------------------------------------------------


def test_two_distinct_ban_patterns_each_fire_once(tmp_path: Path) -> None:
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
    body = (
        f"{subject}\n"
        "\n"
        "Body.\n"
        "\n"
        "Co-Authored-By: Claude Opus <noreply@anthropic.com>\n"
        "Forbidden-Trailer: leaked\n"
    )
    _write_state(folder, [{"sha": sha, "phase": "spec", "subject": subject}])
    scripts = _script_commit(sha, body)
    findings = validate_git_conventions(folder, runner=_ScriptedRunner(scripts))
    blockers = [f for f in findings if f.severity == "BLOCK"]
    assert len(blockers) == 2, blockers

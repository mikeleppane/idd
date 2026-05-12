"""Real-git integration tests for ``validate_git_conventions``.

The unit suite in ``tests/tools/test_validate_git_conventions*.py`` mocks the
git runner, so any defect in the production argv that ``_fetch_message``
emits is invisible to those tests: the canned scripts return whatever the
test author hand-wrote regardless of how the production code actually
invokes git. These tests close that gap by spinning up a real temporary git
repository, making real commits, and calling ``validate_git_conventions``
with the production default runner. A regression in the argv (e.g. a stray
``--`` end-of-options separator that turns the SHA into a pathspec for
``git show``) surfaces here as a silent empty message body and a false
negative on the trailer-ban rule.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from tools.validate.git_conventions import (
    _default_runner,
    _fetch_message,
    validate_git_conventions,
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None,
    reason="real git binary required for production-path validation",
)


_GIT_AUTHOR_ENV: Mapping[str, str] = {
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    # Force a stable initial branch so newer/older git defaults stay quiet.
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
}


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(_GIT_AUTHOR_ENV)
    return env


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        env=_git_env(),
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(repo)],
        env=_git_env(),
        check=True,
        capture_output=True,
        text=True,
    )


def _commit(repo: Path, message: str, filename: str = "f.txt", content: str = "x") -> str:
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo, "add", filename)
    _git(repo, "commit", "-q", "-m", message)
    result = _git(repo, "rev-parse", "HEAD")
    return result.stdout.strip()


def _write_feature_state(repo: Path, sha: str, subject: str) -> Path:
    feature_id = "2026-05-12-real-git"
    folder = repo / ".forge" / "features" / feature_id
    folder.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "feature_id": feature_id,
        "tier": "focused",
        "current_phase": "refine",
        "phases": {},
        "skipped": [],
        "deviations": [],
        "commits": [{"sha": sha, "phase": "refine", "subject": subject}],
    }
    (folder / "state.json").write_text(json.dumps(payload), encoding="utf-8")
    return folder


def _write_config(repo: Path, patterns: list[str]) -> None:
    forge = repo / ".forge"
    forge.mkdir(exist_ok=True)
    payload = {
        "git_conventions": {
            "trailers": {"ban_patterns": patterns},
        },
    }
    (forge / "config.json").write_text(json.dumps(payload), encoding="utf-8")


# --- Direct ``_fetch_message`` probe (root-cause level) ----------------------


def test_fetch_message_returns_real_commit_body(tmp_path: Path) -> None:
    """``_fetch_message`` must return the commit body, not an empty diff.

    This is the smallest test that nails the original defect: the previous
    argv passed ``--`` before the SHA, which made ``git show`` treat the
    SHA as a pathspec and print the (empty) diff for that pathspec instead
    of the commit body. A regression to the broken argv reverts this test
    to an empty string.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    body = "feat(test): seed\n\nCo-Authored-By: Claude <x>\n"
    sha = _commit(repo, body)

    message = _fetch_message(_default_runner, sha, cwd=repo)

    assert message is not None
    assert message.startswith("feat(test): seed")
    assert "Co-Authored-By: Claude <x>" in message


# --- End-to-end trailer-ban firing through ``validate_git_conventions`` ------


def test_banned_trailer_fires_against_real_git(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    body = "feat(test): seed\n\nCo-Authored-By: Claude <x>\n"
    sha = _commit(repo, body)
    _write_config(repo, ["Co-Authored-By: Claude.*"])
    folder = _write_feature_state(repo, sha, "feat(test): seed")

    findings = validate_git_conventions(folder)

    blocks = [f for f in findings if f.severity == "BLOCK"]
    assert blocks, (
        "expected a BLOCK Finding for the banned trailer; "
        f"got findings={[(f.severity, f.message) for f in findings]}"
    )
    assert any(
        "Co-Authored-By: Claude <x>" in f.message and "forbidden trailer" in f.message
        for f in blocks
    ), [(f.severity, f.message) for f in blocks]


def test_malformed_subject_and_banned_trailer_both_fire(tmp_path: Path) -> None:
    """A real commit with both defects must emit BLOCK + HIGH findings.

    Guards against regressions where ``_fetch_message`` returns the wrong
    string and the Conventional Commits grammar check silently passes on
    an empty subject.
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    body = "WIP foo\n\nCo-Authored-By: Claude <x>\n"
    sha = _commit(repo, body)
    _write_config(repo, ["Co-Authored-By: Claude.*"])
    folder = _write_feature_state(repo, sha, "WIP foo")

    findings = validate_git_conventions(folder)

    severities = {f.severity for f in findings}
    assert "BLOCK" in severities, [(f.severity, f.message) for f in findings]
    assert "HIGH" in severities, [(f.severity, f.message) for f in findings]
    assert any("Conventional Commits" in f.message for f in findings if f.severity == "HIGH"), [
        (f.severity, f.message) for f in findings
    ]
    assert any("forbidden trailer" in f.message for f in findings if f.severity == "BLOCK"), [
        (f.severity, f.message) for f in findings
    ]


def test_clean_commit_emits_no_findings(tmp_path: Path) -> None:
    """A well-formed commit with no banned trailer must produce zero findings."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    body = "feat(test): seed\n\nA short body line.\n"
    sha = _commit(repo, body)
    _write_config(repo, ["Co-Authored-By: Claude.*"])
    folder = _write_feature_state(repo, sha, "feat(test): seed")

    findings = validate_git_conventions(folder)

    assert findings == [], [(f.severity, f.message) for f in findings]

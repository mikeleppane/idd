"""End-to-end smoke for the cross-AI auto-mode round trip.

Walks the production auto-mode pipeline against the shipped mock fixture
so any contract drift between the dispatcher, the parser, and the merge
helper surfaces here before it reaches the orchestrating skill. No
mocks, no patches — the dispatcher really shells out to the fixture
script via :func:`subprocess.run` and the captured stdout flows through
the same parser the manual-mode round trip exercises.

The five tests cover the four documented dispatch outcomes plus the
write-response-to-disk persistence step:

1. :func:`test_auto_dispatch_clean_response` — happy path (clean fixture
   branch). Asserts the dispatch result fields and persists the response
   to disk via :func:`tools.cross_ai.dispatch.write_response_to_disk`.
2. :func:`test_auto_dispatch_with_findings_response` — full pipeline
   through parsing and merge into a seeded ``REVIEW.plan.md`` template.
3. :func:`test_auto_dispatch_timeout_falls_back` — hung CLI past the
   wall-clock budget. Asserts ``DispatchError`` with
   :class:`subprocess.TimeoutExpired` on ``__cause__`` and that retry
   did NOT fire (timeout is structurally stuck — see ``dispatch.py``).
4. :func:`test_auto_dispatch_fail_retries_then_falls_back` — non-zero
   exit. Asserts retry fired (via the injected ``sleep`` callable) and
   the wrapped error carries :class:`subprocess.CalledProcessError`.
5. :func:`test_auto_dispatch_oserror_falls_back` — missing binary.
   Asserts ``DispatchError`` with :class:`FileNotFoundError` on
   ``__cause__`` (an :class:`OSError` subclass) and no retry.

All five tests build their environment via :func:`_fixture_env` so the
PATH lookup resolves to the shipped mock script and the system shebang
interpreter (``/usr/bin/env bash``) is still reachable — see the
helper's docstring for the rationale on the system PATH tail.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tools.cross_ai.config import RetryPolicy
from tools.cross_ai.dispatch import (
    DispatchError,
    DispatchResult,
    auto_dispatch,
    write_response_to_disk,
)
from tools.cross_ai.manual import merge_findings_into_review
from tools.cross_ai.parse import parse_response
from tools.cross_ai.prompt import PromptTarget

# Repo root is two levels above this file (tests/smoke/<this>).
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_FIXTURE_DIR: Path = _REPO_ROOT / "tests" / "fixtures" / "_cross_ai"

_FEATURE_ID: str = "2026-05-11-feat-auto-flow"


def _fixture_env(response_kind: str) -> dict[str, str]:
    """Return the env dict that drives the mock dispatch fixture.

    ``PATH`` lists the fixture directory FIRST so the basename lookup
    (``codex`` / ``claude`` / ``gemini``) resolves to the shipped
    symlink rather than any system binary that might shadow it. The
    standard system directories stay on the tail because the fixture's
    ``#!/usr/bin/env bash`` shebang re-resolves ``bash`` through
    ``PATH`` and we cannot strand the interpreter without breaking the
    invocation entirely.
    """
    system_path = ":".join(("/usr/local/bin", "/usr/bin", "/bin"))
    return {
        "PATH": f"{_FIXTURE_DIR}:{system_path}",
        "MOCK_CLI_RESPONSE": response_kind,
    }


def _seed_review_template(repo_root: Path) -> Path:
    """Seed a minimal ``REVIEW.plan.md`` so the merge helper has a target.

    The shape mirrors the manual-mode smoke fixture: frontmatter +
    ``# Findings`` heading + header + separator + one seed row. The
    merge helper appends new rows after the existing data block so the
    seed row pins the insert position.
    """
    feature_dir = repo_root / ".forge" / "features" / _FEATURE_ID
    feature_dir.mkdir(parents=True, exist_ok=True)
    review_path = feature_dir / "REVIEW.plan.md"
    review_path.write_text(
        "---\n"
        f"spec: {_FEATURE_ID}\n"
        "target: plan\n"
        "status: open\n"
        "cycles: 1\n"
        "---\n"
        "\n"
        "# Findings\n"
        "\n"
        "| ID | Severity | Status | Location | Problem | Recommended Fix | Source |\n"
        "|----|----------|--------|----------|---------|-----------------|--------|\n"
        "| F-0 | LOW | open | PLAN.md | seed row | seed fix | self |\n"
        "\n"
        "# Decision\n"
        "\n"
        "<resolved>\n",
        encoding="utf-8",
    )
    return review_path


# --- 1. Happy path: clean response ----------------------------------------


def test_auto_dispatch_clean_response(tmp_path: Path) -> None:
    """``MOCK_CLI_RESPONSE=clean`` → DispatchResult populated, response persisted to disk."""
    result = auto_dispatch(
        cli="codex",
        prompt_text="<smoke prompt>",
        timeout_seconds=10,
        env=_fixture_env("clean"),
    )
    assert isinstance(result, DispatchResult)
    assert result.exit_code == 0
    assert result.attempts == 1
    assert "No findings." in result.response_text

    response_path = write_response_to_disk(
        result.response_text,
        _FEATURE_ID,
        PromptTarget.plan,
        tmp_path,
    )
    assert response_path.exists()
    # Filename convention: ``<target>-<utc>-response.md`` under the
    # feature's ``cross-ai/`` directory.
    assert response_path.name.startswith("plan-")
    assert response_path.name.endswith("-response.md")
    assert response_path.parent.name == "cross-ai"
    assert response_path.read_text(encoding="utf-8") == result.response_text


# --- 2. Findings flow through parse + merge -------------------------------


def test_auto_dispatch_with_findings_response(tmp_path: Path) -> None:
    """``MOCK_CLI_RESPONSE=with-findings`` → 2 rows parsed and merged into REVIEW."""
    review_path = _seed_review_template(tmp_path)

    result = auto_dispatch(
        cli="codex",
        prompt_text="<smoke prompt>",
        timeout_seconds=10,
        env=_fixture_env("with-findings"),
    )
    assert result.exit_code == 0
    assert result.attempts == 1
    # Sanity: the canned fixture's row IDs reached the dispatcher's stdout.
    assert "F-1" in result.response_text
    assert "F-2" in result.response_text

    findings = parse_response(result.response_text, reviewer_id="codex", target="plan")
    assert len(findings) == 2
    assert findings[0].id == "F-1"
    assert findings[0].severity == "HIGH"
    assert findings[1].id == "F-2"
    assert findings[1].severity == "MEDIUM"
    # Source column is dispatcher-injected; the reviewer's value is
    # dropped by design (see ``parse_response`` docstring).
    assert all(f.source == "external-codex" for f in findings)

    appended = merge_findings_into_review(findings, PromptTarget.plan, _FEATURE_ID, tmp_path)
    assert appended == 2

    review_text = review_path.read_text(encoding="utf-8")
    seed_idx = review_text.index("| F-0 |")
    f1_idx = review_text.index("| F-1 |")
    f2_idx = review_text.index("| F-2 |")
    assert seed_idx < f1_idx < f2_idx
    # Constitution tag in the Problem column survives the round trip.
    assert "[constitution:A1]" in review_text


# --- 3. Timeout: no retry, TimeoutExpired on __cause__ --------------------


def test_auto_dispatch_timeout_falls_back() -> None:
    """``MOCK_CLI_RESPONSE=timeout`` past the budget → DispatchError, no retry.

    The dispatcher contract is that :class:`subprocess.TimeoutExpired`
    does NOT retry — a hung CLI is structurally stuck and another shell
    out only compounds the wait. We assert this by injecting a
    list-append ``sleep`` callable: a retry would push at least one
    backoff entry, so an empty list proves the loop bailed on the first
    attempt.
    """
    sleeps: list[float] = []
    with pytest.raises(DispatchError) as exc_info:
        auto_dispatch(
            cli="codex",
            prompt_text="<smoke prompt>",
            timeout_seconds=1,
            retry=RetryPolicy(max=2, backoff_seconds=0),
            env=_fixture_env("timeout"),
            sleep=sleeps.append,
        )

    assert isinstance(exc_info.value.__cause__, subprocess.TimeoutExpired)
    # No retry on timeout — the sleep callable was never invoked.
    assert sleeps == []


# --- 4. Non-zero exit: retry fires, CalledProcessError on __cause__ -------


def test_auto_dispatch_fail_retries_then_falls_back() -> None:
    """``MOCK_CLI_RESPONSE=fail`` + ``retry.max=1`` → retry, then DispatchError.

    The dispatcher contract is that :class:`subprocess.CalledProcessError`
    DOES retry up to ``retry.max + 1`` total attempts — a non-zero exit
    is the one failure where another invocation might succeed (CLI
    transient rate limit, network blip, etc.). We assert the retry
    fired by injecting a list-append ``sleep`` callable: with
    ``retry.max=1`` the dispatcher must pause exactly once with the
    configured ``backoff_seconds`` value before the second attempt.
    """
    sleeps: list[float] = []
    with pytest.raises(DispatchError) as exc_info:
        auto_dispatch(
            cli="codex",
            prompt_text="<smoke prompt>",
            timeout_seconds=10,
            retry=RetryPolicy(max=1, backoff_seconds=0),
            env=_fixture_env("fail"),
            sleep=sleeps.append,
        )

    assert isinstance(exc_info.value.__cause__, subprocess.CalledProcessError)
    # ``retry.max=1`` → 2 total attempts → exactly 1 backoff sleep
    # between them (no trailing sleep after the final failure).
    assert sleeps == [0.0]


# --- 5. Missing binary: no retry, FileNotFoundError on __cause__ ----------


def test_auto_dispatch_oserror_falls_back() -> None:
    """Missing CLI binary → DispatchError, no retry, ``FileNotFoundError`` cause.

    PATH is restricted to the fixture directory so the made-up CLI name
    cannot be shadowed by any system binary. The dispatcher contract is
    that :class:`OSError` (and its :class:`FileNotFoundError` subclass)
    does NOT retry — the binary is structurally unavailable and another
    invocation cannot change that.
    """
    sleeps: list[float] = []
    with pytest.raises(DispatchError) as exc_info:
        auto_dispatch(
            cli="nonexistent_cli_xyz_p3",
            prompt_text="<smoke prompt>",
            timeout_seconds=10,
            retry=RetryPolicy(max=2, backoff_seconds=0),
            env={"PATH": str(_FIXTURE_DIR)},
            sleep=sleeps.append,
        )

    assert isinstance(exc_info.value.__cause__, FileNotFoundError)
    # No retry on OSError — the sleep callable was never invoked.
    assert sleeps == []

"""Tests for ``tools.cross_ai.dispatch`` — auto-mode dispatch helpers.

Three helpers under test:

* ``auto_dispatch`` — sync ``subprocess.run`` wrapper around an external
  reviewer CLI. Three failure axes verified independently because each
  one routes differently in production:

  - ``CalledProcessError`` (non-zero exit) **does** retry up to
    ``RetryPolicy.max + 1`` total attempts with ``sleep(backoff_seconds)``
    between them. The injected ``sleep`` seam keeps the suite fast and
    deterministic.
  - ``TimeoutExpired`` (CLI hung past ``timeout_seconds``) does **not**
    retry — re-running a hung binary compounds risk rather than
    resolving it.
  - ``OSError`` (missing binary, unexecutable, etc.) does **not**
    retry — the binary is structurally unavailable; another shell-out
    will fail identically.
* ``record_dispatch_approval`` — atomic write of the cross_ai dispatch
  approval marker to ``.forge/config.json``. Idempotent: a second call
  when the field is already present is a no-op (verified by hashing the
  on-disk bytes before/after).
* ``write_response_to_disk`` — atomic write of a captured reviewer
  response to ``.forge/features/<id>/cross-ai/<target>-<ts>-response.md``.
  Mirrors P2.1 ``write_prompt_to_disk`` shape; the timestamp seam keeps
  the filename deterministic under test.

Subprocess wiring uses the ``tests/fixtures/_cross_ai/`` mock fixture
shipped earlier. The fixture exposes three CLI symlinks (``codex``,
``claude``, ``gemini``) that all dispatch through ``mock_dispatch.sh``;
behavior is selected via the ``MOCK_CLI_RESPONSE`` env var
(``clean`` / ``with-findings`` / ``timeout`` / ``fail``). Tests pass
``env={"PATH": <fixture_dir>, "MOCK_CLI_RESPONSE": ...}`` and call the
CLI by basename so the fixture's PATH lookup resolves to the symlink.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tools.cross_ai.config import CrossAiMode, RetryPolicy, load_config
from tools.cross_ai.dispatch import (
    DispatchError,
    DispatchResult,
    auto_dispatch,
    record_dispatch_approval,
    write_response_to_disk,
)
from tools.cross_ai.prompt import PromptTarget

# --- shared fixtures -------------------------------------------------------


_FIXTURE_DIR: Path = Path(__file__).resolve().parents[1] / "fixtures" / "_cross_ai"


def _fixture_env(response_kind: str) -> dict[str, str]:
    """Return the env dict that drives the mock dispatch fixture.

    ``PATH`` lists the fixture directory FIRST so the basename lookup
    (``codex`` / ``claude`` / ``gemini``) resolves to the symlink
    rather than any system binary that might shadow it; the standard
    system directories are kept on the tail because the fixture's
    ``#!/usr/bin/env bash`` shebang re-resolves ``bash`` through
    ``PATH`` and we cannot strand the interpreter without breaking the
    invocation entirely.
    """
    system_path = ":".join(("/usr/local/bin", "/usr/bin", "/bin"))
    return {
        "PATH": f"{_FIXTURE_DIR}:{system_path}",
        "MOCK_CLI_RESPONSE": response_kind,
    }


# --- auto_dispatch happy paths --------------------------------------------


def test_auto_dispatch_clean_response_returns_dispatch_result() -> None:
    """(a) Mock ``clean`` → ``DispatchResult`` populated, response_text contains expected sentinel."""
    result = auto_dispatch(
        "codex",
        prompt_text="ignored by mock",
        timeout_seconds=10,
        env=_fixture_env("clean"),
    )
    assert isinstance(result, DispatchResult)
    assert result.cli == "codex"
    assert result.exit_code == 0
    assert result.attempts == 1
    assert "No findings." in result.response_text


def test_auto_dispatch_with_findings_response_carries_table_rows() -> None:
    """(b) Mock ``with-findings`` → response_text contains finding rows."""
    result = auto_dispatch(
        "claude",
        prompt_text="ignored",
        timeout_seconds=10,
        env=_fixture_env("with-findings"),
    )
    # The canned with-findings fixture ships a Markdown findings table;
    # asserting on the row delimiter avoids coupling to any specific
    # finding string while still proving rows reached the caller.
    assert "|" in result.response_text
    assert result.attempts == 1
    assert result.exit_code == 0


def test_auto_dispatch_custom_env_overrides_default_behavior() -> None:
    """(c) Custom ``env={"MOCK_CLI_RESPONSE": "clean"}`` overrides default branch."""
    # Default behavior of mock when MOCK_CLI_RESPONSE is unset is "clean";
    # explicitly passing the env proves the override path is honored.
    env = _fixture_env("clean")
    result = auto_dispatch("gemini", prompt_text="x", timeout_seconds=5, env=env)
    assert "No findings." in result.response_text


def test_auto_dispatch_records_elapsed_seconds_and_attempts() -> None:
    """(d) ``elapsed_seconds`` non-negative; ``attempts`` matches the success path."""
    result = auto_dispatch(
        "codex",
        prompt_text="x",
        timeout_seconds=10,
        env=_fixture_env("clean"),
    )
    assert result.elapsed_seconds >= 0.0
    assert result.attempts == 1


# --- auto_dispatch failure paths ------------------------------------------


def test_auto_dispatch_called_process_error_retries_until_exhaustion() -> None:
    """(e) ``fail`` exit + ``retry.max=1`` → 2 attempts then ``DispatchError`` (cause=CalledProcessError)."""
    sleeps: list[float] = []
    with pytest.raises(DispatchError) as exc_info:
        auto_dispatch(
            "codex",
            prompt_text="x",
            timeout_seconds=10,
            retry=RetryPolicy(max=1, backoff_seconds=0),
            env=_fixture_env("fail"),
            sleep=sleeps.append,
        )

    assert isinstance(exc_info.value.__cause__, subprocess.CalledProcessError)
    assert "2 attempts" in str(exc_info.value)
    # One sleep between the two attempts.
    assert len(sleeps) == 1


def test_auto_dispatch_called_process_error_no_retries_when_max_zero() -> None:
    """(f) ``fail`` exit + ``retry.max=0`` → exactly 1 attempt before raising."""
    sleeps: list[float] = []
    with pytest.raises(DispatchError) as exc_info:
        auto_dispatch(
            "codex",
            prompt_text="x",
            timeout_seconds=10,
            retry=RetryPolicy(max=0, backoff_seconds=0),
            env=_fixture_env("fail"),
            sleep=sleeps.append,
        )
    assert "1 attempts" in str(exc_info.value)
    assert sleeps == []  # No retry → no sleep.


def test_auto_dispatch_sleep_called_with_backoff_between_attempts() -> None:
    """(g) Injected ``sleep`` is called with ``retry.backoff_seconds`` between attempts."""
    sleeps: list[float] = []
    with pytest.raises(DispatchError):
        auto_dispatch(
            "codex",
            prompt_text="x",
            timeout_seconds=10,
            retry=RetryPolicy(max=2, backoff_seconds=7),
            env=_fixture_env("fail"),
            sleep=sleeps.append,
        )
    # Three attempts → two backoff sleeps, both at 7 seconds.
    assert sleeps == [7.0, 7.0]


def test_auto_dispatch_missing_binary_raises_without_retry() -> None:
    """(h) Nonexistent CLI → ``DispatchError`` (cause=FileNotFoundError); attempts=1."""
    sleeps: list[float] = []
    with pytest.raises(DispatchError) as exc_info:
        auto_dispatch(
            "nonexistent_cli_xyz_unlikely_to_exist",
            prompt_text="x",
            timeout_seconds=10,
            retry=RetryPolicy(max=3, backoff_seconds=0),
            # Restrict PATH to the fixture dir so the made-up CLI name
            # truly fails the lookup (no system bin can shadow it).
            env={"PATH": str(_FIXTURE_DIR)},
            sleep=sleeps.append,
        )
    assert isinstance(exc_info.value.__cause__, FileNotFoundError)
    # No retry on OSError — sleep must never have fired.
    assert sleeps == []
    assert "could not invoke" in str(exc_info.value)


def test_auto_dispatch_timeout_raises_without_retry() -> None:
    """(i) ``timeout`` mock + 1s budget → ``DispatchError`` (cause=TimeoutExpired); attempts=1."""
    sleeps: list[float] = []
    with pytest.raises(DispatchError) as exc_info:
        auto_dispatch(
            "codex",
            prompt_text="x",
            timeout_seconds=1,
            retry=RetryPolicy(max=3, backoff_seconds=0),
            env=_fixture_env("timeout"),
            sleep=sleeps.append,
        )
    assert isinstance(exc_info.value.__cause__, subprocess.TimeoutExpired)
    assert sleeps == []
    assert "timed out after 1s" in str(exc_info.value)


# --- record_dispatch_approval ---------------------------------------------


def _read_config(repo_root: Path) -> dict[str, object]:
    raw = (repo_root / ".forge" / "config.json").read_text(encoding="utf-8")
    parsed: dict[str, object] = json.loads(raw)
    return parsed


def test_record_dispatch_approval_creates_missing_config(tmp_path: Path) -> None:
    """(j) Missing config.json → file created with the cross_ai approval block."""
    fixed = datetime(2026, 5, 11, 12, 30, 45, tzinfo=UTC)
    record_dispatch_approval(tmp_path, now=fixed, by="alice")

    document = _read_config(tmp_path)
    cross_ai = document["cross_ai"]
    assert isinstance(cross_ai, dict)
    assert cross_ai["dispatch_approved_at"] == "2026-05-11T12:30:45+00:00"
    assert cross_ai["dispatch_approved_by"] == "alice"


def test_record_dispatch_approval_preserves_other_keys(tmp_path: Path) -> None:
    """(k) Existing config without cross_ai block → block added, other keys preserved."""
    config_path = tmp_path / ".forge" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"unrelated": {"keep": "me"}, "another": 7}),
        encoding="utf-8",
    )

    record_dispatch_approval(tmp_path, now=datetime(2026, 1, 1, tzinfo=UTC), by="bob")
    document = _read_config(tmp_path)

    assert document["unrelated"] == {"keep": "me"}
    assert document["another"] == 7
    # ``mode`` is seeded to ``"manual"`` when the cross_ai block is
    # created from scratch — required by the schema so a subsequent
    # ``load_config`` does not fail with ``'mode' is a required property``.
    assert document["cross_ai"] == {
        "mode": "manual",
        "dispatch_approved_at": "2026-01-01T00:00:00+00:00",
        "dispatch_approved_by": "bob",
    }


def test_record_dispatch_approval_is_idempotent(tmp_path: Path) -> None:
    """(l) Existing dispatch_approved_at → no overwrite; on-disk bytes unchanged."""
    config_path = tmp_path / ".forge" / "config.json"
    config_path.parent.mkdir(parents=True)
    seed = {
        "cross_ai": {
            "dispatch_approved_at": "2025-12-01T00:00:00+00:00",
            "dispatch_approved_by": "originaluser",
        }
    }
    config_path.write_text(json.dumps(seed), encoding="utf-8")
    before = config_path.read_bytes()

    record_dispatch_approval(
        tmp_path,
        now=datetime(2026, 5, 11, tzinfo=UTC),
        by="laterperson",
    )
    after = config_path.read_bytes()

    assert before == after  # Bytes-identical confirms no rewrite at all.


def test_record_dispatch_approval_honors_now_and_by_overrides(tmp_path: Path) -> None:
    """(m) ``now=`` and ``by=`` overrides surface verbatim in the written block."""
    fixed = datetime(2026, 7, 4, 9, 0, 0, tzinfo=UTC)
    record_dispatch_approval(tmp_path, now=fixed, by="custom-user")
    cross_ai = _read_config(tmp_path)["cross_ai"]
    assert isinstance(cross_ai, dict)
    assert cross_ai["dispatch_approved_at"] == "2026-07-04T09:00:00+00:00"
    assert cross_ai["dispatch_approved_by"] == "custom-user"


def test_record_dispatch_approval_round_trips_through_load_config(tmp_path: Path) -> None:
    """Written config must be schema-clean: ``load_config`` returns a populated ``CrossAiConfig``.

    Regression for the schema-invalid create path: the schema requires
    ``cross_ai.mode``, so creating the block without seeding ``mode``
    surfaced as ``CrossAiConfigError 'mode' is a required property at []``
    the next time anything called :func:`load_config`. This test pins the
    round-trip: write → load → all fields populated, no error.
    """
    fixed = datetime(2026, 5, 11, 12, 0, 0, tzinfo=UTC)
    record_dispatch_approval(tmp_path, now=fixed, by="alice")

    cfg = load_config(tmp_path)
    assert cfg.mode == CrossAiMode.manual
    assert cfg.dispatch_approved_at == "2026-05-11T12:00:00+00:00"
    assert cfg.dispatch_approved_by == "alice"


def test_record_dispatch_approval_preserves_existing_mode(tmp_path: Path) -> None:
    """``mode`` already set on the cross_ai block survives unchanged (auto stays auto)."""
    config_path = tmp_path / ".forge" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"cross_ai": {"mode": "auto", "allowed_clis": ["codex"]}}),
        encoding="utf-8",
    )

    record_dispatch_approval(tmp_path, now=datetime(2026, 5, 11, tzinfo=UTC), by="alice")

    cfg = load_config(tmp_path)
    # ``auto`` mode preserved — the helper must not clobber a real config
    # decision back to the default ``manual``.
    assert cfg.mode == CrossAiMode.auto
    assert cfg.allowed_clis == ("codex",)
    assert cfg.dispatch_approved_at == "2026-05-11T00:00:00+00:00"
    assert cfg.dispatch_approved_by == "alice"


def test_record_dispatch_approval_atomic_write_leaves_no_temp_artifacts(tmp_path: Path) -> None:
    """(n) After a successful write, only ``config.json`` exists in ``.forge/`` (no ``.tmp.*``)."""
    record_dispatch_approval(
        tmp_path,
        now=datetime(2026, 5, 11, tzinfo=UTC),
        by="alice",
    )
    forge_dir = tmp_path / ".forge"
    children = sorted(p.name for p in forge_dir.iterdir())
    assert children == ["config.json"]


# --- write_response_to_disk -----------------------------------------------


def test_write_response_to_disk_writes_at_canonical_path(tmp_path: Path) -> None:
    """(o) Writes file at the canonical cross-ai response path; content matches input."""
    fixed = datetime(2026, 5, 11, 14, 25, 30, tzinfo=UTC)
    body = "# Findings\n\n| ID | Severity | ... |\n"

    written = write_response_to_disk(
        body,
        feature_id="feat-xyz",
        target=PromptTarget.code,
        repo_root=tmp_path,
        now=fixed,
    )

    expected = (
        tmp_path
        / ".forge"
        / "features"
        / "feat-xyz"
        / "cross-ai"
        / "code-2026-05-11T14-25-30Z-response.md"
    )
    assert written == expected.resolve()
    assert written.read_text(encoding="utf-8") == body


# --- typing sanity --------------------------------------------------------


def test_sleep_callable_type_accepted_by_static_signature() -> None:
    """Compile-time guard: the ``sleep`` parameter accepts a real Callable[[float], None]."""
    captured: list[float] = []

    def fake_sleep(seconds: float) -> None:
        captured.append(seconds)

    handle: Callable[[float], None] = fake_sleep
    # The call below also asserts at runtime that the type alias resolves.
    with pytest.raises(DispatchError):
        auto_dispatch(
            "codex",
            prompt_text="x",
            timeout_seconds=5,
            retry=RetryPolicy(max=1, backoff_seconds=0),
            env=_fixture_env("fail"),
            sleep=handle,
        )
    assert captured == [0.0]

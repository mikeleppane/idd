"""Tests for `tools.harden.uat_replay` — replays UAT prompts post-ship."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.harden.contract import HardenError
from tools.harden.uat_replay import (
    UATOutcome,
    UATPrompt,
    UATResult,
    run_uat_replay,
)


def _write_verification(repo_root: Path, feature_id: str, body: str) -> Path:
    feature_dir = repo_root / ".forge" / "features" / feature_id
    feature_dir.mkdir(parents=True, exist_ok=True)
    target = feature_dir / "VERIFICATION.md"
    target.write_text(body, encoding="utf-8")
    return target


def _uat_section(*prompts: str) -> str:
    rendered = "# UAT\n\n"
    for prompt in prompts:
        rendered += f"- {prompt}\n"
    rendered += "\n"
    return rendered


def test_uat_replay_no_verification_returns_skipped(tmp_path: Path) -> None:
    feature_id = "2026-05-09-uat-missing"
    # Feature folder is not even created.
    result = run_uat_replay(tmp_path, feature_id, mode="interactive")

    assert isinstance(result, UATResult)
    assert result.status == "skipped"
    assert result.mode == "interactive"
    assert result.prompts_replayed == 0
    assert result.prompts_confirmed == 0
    assert result.outcomes == []


def test_uat_replay_no_prompts_returns_skipped(tmp_path: Path) -> None:
    feature_id = "2026-05-09-uat-noprompts"
    _write_verification(
        tmp_path,
        feature_id,
        "# Coverage\n\nnothing to replay\n",
    )

    result = run_uat_replay(tmp_path, feature_id, mode="interactive")

    assert result.status == "skipped"
    assert result.prompts_replayed == 0


def test_uat_replay_interactive_all_confirmed_passes(tmp_path: Path) -> None:
    feature_id = "2026-05-09-uat-pass"
    _write_verification(
        tmp_path,
        feature_id,
        _uat_section(
            "Did the CLI accept the new flag without crashing?",
            "Did the output JSON include the new field?",
        ),
    )

    seen: list[str] = []

    def prompter(prompt: UATPrompt) -> UATOutcome:
        seen.append(prompt.prompt_id)
        return UATOutcome(prompt_id=prompt.prompt_id, status="confirmed", detail="yes")

    result = run_uat_replay(tmp_path, feature_id, mode="interactive", prompter=prompter)

    assert seen == ["prompt-1", "prompt-2"]
    assert result.status == "pass"
    assert result.mode == "interactive"
    assert result.prompts_replayed == 2
    assert result.prompts_confirmed == 2
    assert all(outcome.status == "confirmed" for outcome in result.outcomes)


def test_uat_replay_interactive_one_disconfirmed_fails(tmp_path: Path) -> None:
    feature_id = "2026-05-09-uat-fail"
    _write_verification(
        tmp_path,
        feature_id,
        _uat_section(
            "Did the CLI accept the new flag?",
            "Did the audit log record the run?",
        ),
    )

    def prompter(prompt: UATPrompt) -> UATOutcome:
        if prompt.prompt_id == "prompt-2":
            return UATOutcome(
                prompt_id=prompt.prompt_id,
                status="disconfirmed",
                detail="no audit row",
            )
        return UATOutcome(prompt_id=prompt.prompt_id, status="confirmed", detail="yes")

    result = run_uat_replay(tmp_path, feature_id, mode="interactive", prompter=prompter)

    assert result.status == "fail"
    assert result.prompts_replayed == 2
    assert result.prompts_confirmed == 1


def test_uat_replay_default_prompter_skips(tmp_path: Path) -> None:
    feature_id = "2026-05-09-uat-default"
    _write_verification(
        tmp_path,
        feature_id,
        _uat_section(
            "Did the build succeed?",
            "Did the README render?",
        ),
    )

    result = run_uat_replay(tmp_path, feature_id, mode="interactive")

    assert result.status == "partial"
    assert result.prompts_replayed == 2
    assert result.prompts_confirmed == 0
    assert all(outcome.status == "skipped" for outcome in result.outcomes)


def test_uat_replay_non_interactive_replays_transcript(tmp_path: Path) -> None:
    feature_id = "2026-05-09-uat-transcript"
    _write_verification(
        tmp_path,
        feature_id,
        _uat_section(
            "Did the install run?",
            "Did the smoke test pass?",
        ),
    )

    transcript_path = tmp_path / "transcript.jsonl"
    transcript_path.write_text(
        "\n".join(
            [
                json.dumps({"prompt_id": "prompt-1", "status": "confirmed", "detail": "ok"}),
                json.dumps({"prompt_id": "prompt-2", "status": "confirmed", "detail": "green"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_uat_replay(
        tmp_path,
        feature_id,
        mode="non-interactive",
        transcript_path=transcript_path,
    )

    assert result.status == "pass"
    assert result.mode == "non-interactive"
    assert result.prompts_replayed == 2
    assert result.prompts_confirmed == 2


def test_uat_replay_non_interactive_missing_transcript_raises(tmp_path: Path) -> None:
    feature_id = "2026-05-09-uat-no-transcript"
    _write_verification(
        tmp_path,
        feature_id,
        _uat_section("Did anything happen?"),
    )

    with pytest.raises(HardenError, match=r"transcript_path required"):
        run_uat_replay(tmp_path, feature_id, mode="non-interactive")


def test_uat_replay_non_interactive_partial_transcript(tmp_path: Path) -> None:
    feature_id = "2026-05-09-uat-partial"
    _write_verification(
        tmp_path,
        feature_id,
        _uat_section(
            "Did the install run?",
            "Did the smoke test pass?",
            "Did the audit log capture the run?",
        ),
    )

    transcript_path = tmp_path / "transcript.jsonl"
    transcript_path.write_text(
        json.dumps({"prompt_id": "prompt-1", "status": "confirmed", "detail": "ok"}) + "\n",
        encoding="utf-8",
    )

    result = run_uat_replay(
        tmp_path,
        feature_id,
        mode="non-interactive",
        transcript_path=transcript_path,
    )

    assert result.status == "partial"
    assert result.prompts_replayed == 3
    assert result.prompts_confirmed == 1
    statuses = [outcome.status for outcome in result.outcomes]
    assert statuses == ["confirmed", "skipped", "skipped"]


def test_uat_replay_non_interactive_disconfirmed_fails(tmp_path: Path) -> None:
    feature_id = "2026-05-09-uat-transcript-fail"
    _write_verification(
        tmp_path,
        feature_id,
        _uat_section(
            "Did the install run?",
            "Did the smoke test pass?",
        ),
    )

    transcript_path = tmp_path / "transcript.jsonl"
    transcript_path.write_text(
        "\n".join(
            [
                json.dumps({"prompt_id": "prompt-1", "status": "confirmed", "detail": "ok"}),
                json.dumps(
                    {"prompt_id": "prompt-2", "status": "disconfirmed", "detail": "broke"}
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_uat_replay(
        tmp_path,
        feature_id,
        mode="non-interactive",
        transcript_path=transcript_path,
    )

    assert result.status == "fail"
    assert result.prompts_replayed == 2
    assert result.prompts_confirmed == 1


def test_uat_replay_fence_aware_parse(tmp_path: Path) -> None:
    feature_id = "2026-05-09-uat-fence"
    body = (
        "# UAT\n\n"
        "- Did the real prompt fire?\n\n"
        "Example below — must NOT be parsed:\n\n"
        "```markdown\n"
        "# UAT\n"
        "- Did the fake prompt inside a fence get picked up?\n"
        "```\n"
    )
    _write_verification(tmp_path, feature_id, body)

    seen: list[str] = []

    def prompter(prompt: UATPrompt) -> UATOutcome:
        seen.append(prompt.text)
        return UATOutcome(prompt_id=prompt.prompt_id, status="confirmed", detail="yes")

    result = run_uat_replay(tmp_path, feature_id, mode="interactive", prompter=prompter)

    assert len(seen) == 1
    assert "real prompt" in seen[0]
    assert result.prompts_replayed == 1
    assert result.status == "pass"

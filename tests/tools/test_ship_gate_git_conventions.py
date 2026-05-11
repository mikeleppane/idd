"""Tests for the git-conventions ship-gate wiring helpers in ``tools.ship_gate``.

Cover partition, evaluate, and the two render helpers. ``Finding`` is the
canonical seam — these helpers are pure dispatch over its values, so the tests
construct fixtures directly instead of shelling out to ``git``.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from tools import ship_gate as sg
from tools.validate import Finding

# --- Helpers ------------------------------------------------------------------


def _finding(severity: str, message: str, *, file: Path | None = None) -> Finding:
    """Build a Finding with the git-conventions target wired in."""
    return Finding(
        severity,  # type: ignore[arg-type]  # tests cover defensive out-of-vocab case
        "git-conventions",
        file if file is not None else Path("state.json"),
        message,
    )


def _feature_layout(tmp_path: Path, feature_id: str = "2026-05-11-gx") -> Path:
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


class _ScriptedRunner:
    """Test seam returning canned ``CompletedProcess`` results per git argv."""

    def __init__(self, scripts: dict[tuple[str, ...], tuple[int, str, str]]) -> None:
        self._scripts = scripts

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
        if key not in self._scripts:
            raise AssertionError(f"unscripted git invocation: {key}")
        returncode, stdout, stderr = self._scripts[key]
        return subprocess.CompletedProcess(
            args=args, returncode=returncode, stdout=stdout, stderr=stderr
        )


def _script_unknown_sha(sha: str) -> dict[tuple[str, ...], tuple[int, str, str]]:
    return {
        ("git", "rev-parse", "--verify", f"{sha}^{{commit}}"): (
            128,
            "",
            "fatal: bad revision\n",
        ),
    }


def _script_clean_commit(sha: str, message: str) -> dict[tuple[str, ...], tuple[int, str, str]]:
    # ``git show`` carries the ``--`` end-of-options separator; ``git rev-parse
    # --verify`` does not (the ``--`` makes the arg a pathspec and breaks the
    # verify).
    return {
        ("git", "rev-parse", "--verify", f"{sha}^{{commit}}"): (0, sha + "\n", ""),
        ("git", "show", "-s", "--format=%B", "--", sha): (0, message, ""),
    }


# --- partition_git_conventions ------------------------------------------------


def test_partition_git_conventions_empty_input_returns_three_empty_buckets() -> None:
    partition = sg.partition_git_conventions([])

    assert partition.gate == ()
    assert partition.warn == ()
    assert partition.info == ()


def test_partition_git_conventions_returns_frozen_dataclass_with_tuple_fields() -> None:
    partition = sg.partition_git_conventions([_finding("BLOCK", "abc12345: forbidden trailer")])

    assert dataclasses.is_dataclass(partition)
    assert isinstance(partition.gate, tuple)
    assert isinstance(partition.warn, tuple)
    assert isinstance(partition.info, tuple)
    # Frozen dataclass: assignment must raise.
    try:
        partition.gate = ()  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        pass
    else:  # pragma: no cover - defensive; frozen contract violation
        raise AssertionError("expected FrozenInstanceError on field assignment")


def test_partition_git_conventions_routes_block_to_gate() -> None:
    f = _finding("BLOCK", "abc12345: forbidden trailer matched pattern 'Co-Authored-By: .+'")

    partition = sg.partition_git_conventions([f])

    assert partition.gate == (f,)
    assert partition.warn == ()
    assert partition.info == ()


def test_partition_git_conventions_routes_high_to_gate() -> None:
    f = _finding("HIGH", "abc12345: subject exceeds 72 chars (got 80)")

    partition = sg.partition_git_conventions([f])

    assert partition.gate == (f,)
    assert partition.warn == ()
    assert partition.info == ()


def test_partition_git_conventions_routes_medium_to_warn() -> None:
    f = _finding("MEDIUM", "abc12345: subject capitalization deviates from convention")

    partition = sg.partition_git_conventions([f])

    assert partition.gate == ()
    assert partition.warn == (f,)
    assert partition.info == ()


def test_partition_git_conventions_routes_low_to_info() -> None:
    f = _finding("LOW", "abc12345: trailer order non-canonical")

    partition = sg.partition_git_conventions([f])

    assert partition.gate == ()
    assert partition.warn == ()
    assert partition.info == (f,)


def test_partition_git_conventions_routes_warn_to_info() -> None:
    f = _finding("WARN", "unknown-sha:abc12345deadbeef")

    partition = sg.partition_git_conventions([f])

    assert partition.gate == ()
    assert partition.warn == ()
    assert partition.info == (f,)


def test_partition_git_conventions_routes_out_of_vocab_severity_to_info() -> None:
    # Hand-construct a Finding bypassing the Severity literal — protects
    # against deserialized JSON inputs surfacing an unexpected severity at
    # runtime. mypy would normally reject this, hence the cast.
    f = _finding("UNKNOWN", "abc12345: legacy validator emitted an unfamiliar severity")

    partition = sg.partition_git_conventions([f])

    assert partition.info == (f,)
    assert partition.gate == ()
    assert partition.warn == ()


def test_partition_git_conventions_mixed_findings_split_into_correct_buckets() -> None:
    f_block1 = _finding("BLOCK", "aaaaaaaa: forbidden trailer 1")
    f_block2 = _finding("BLOCK", "bbbbbbbb: forbidden trailer 2")
    f_high = _finding("HIGH", "cccccccc: subject too long")
    f_medium = _finding("MEDIUM", "dddddddd: subject capitalization")
    f_low = _finding("LOW", "eeeeeeee: trailer order")
    f_warn = _finding("WARN", "unknown-sha:ffffffff")

    partition = sg.partition_git_conventions([f_block1, f_block2, f_high, f_medium, f_low, f_warn])

    assert partition.gate == (f_block1, f_block2, f_high)
    assert partition.warn == (f_medium,)
    assert partition.info == (f_low, f_warn)


def test_partition_git_conventions_preserves_input_order_within_buckets() -> None:
    # All three findings sort into the same bucket; partition must keep them
    # in declaration order so callers can reason about determinism.
    first = _finding("BLOCK", "aaaaaaaa: forbidden trailer A")
    second = _finding("HIGH", "bbbbbbbb: subject too long B")
    third = _finding("BLOCK", "cccccccc: forbidden trailer C")

    partition = sg.partition_git_conventions([first, second, third])

    assert partition.gate == (first, second, third)


def test_partition_git_conventions_accepts_generator_input() -> None:
    def _gen() -> Iterator[Finding]:
        yield _finding("BLOCK", "aaaaaaaa: forbidden trailer")
        yield _finding("MEDIUM", "bbbbbbbb: capitalization")

    partition = sg.partition_git_conventions(_gen())

    assert len(partition.gate) == 1
    assert len(partition.warn) == 1
    assert partition.info == ()


# --- evaluate_git_conventions_gate --------------------------------------------


def test_evaluate_git_conventions_gate_empty_commits_returns_empty_partition(
    tmp_path: Path,
) -> None:
    folder = _feature_layout(tmp_path)
    _write_state(folder, commits=[])

    partition = sg.evaluate_git_conventions_gate(folder, runner=_ScriptedRunner({}))

    assert partition.gate == ()
    assert partition.warn == ()
    assert partition.info == ()


def test_evaluate_git_conventions_gate_unknown_sha_lands_in_info_bucket(
    tmp_path: Path,
) -> None:
    folder = _feature_layout(tmp_path)
    sha = "0123456789abcdef" * 2 + "0123"
    _write_state(folder, commits=[{"sha": sha}])

    partition = sg.evaluate_git_conventions_gate(
        folder, runner=_ScriptedRunner(_script_unknown_sha(sha))
    )

    assert partition.gate == ()
    assert partition.warn == ()
    assert len(partition.info) == 1
    only = partition.info[0]
    assert only.severity == "WARN"
    assert only.message == f"unknown-sha:{sha}"


def test_evaluate_git_conventions_gate_routes_subject_too_long_to_gate(
    tmp_path: Path,
) -> None:
    folder = _feature_layout(tmp_path)
    sha = "0123456789abcdef" * 2 + "0123"
    overlong_subject = "feat(tools): " + ("x" * 80)
    _write_state(folder, commits=[{"sha": sha}])

    partition = sg.evaluate_git_conventions_gate(
        folder,
        runner=_ScriptedRunner(_script_clean_commit(sha, overlong_subject + "\n")),
    )

    assert len(partition.gate) == 1
    assert partition.gate[0].severity == "HIGH"
    assert partition.warn == ()
    assert partition.info == ()


def test_evaluate_git_conventions_gate_routes_clean_commit_to_empty_partition(
    tmp_path: Path,
) -> None:
    folder = _feature_layout(tmp_path)
    sha = "0123456789abcdef" * 2 + "0123"
    _write_state(folder, commits=[{"sha": sha}])

    partition = sg.evaluate_git_conventions_gate(
        folder,
        runner=_ScriptedRunner(_script_clean_commit(sha, "feat(tools): tidy helper\n")),
    )

    assert partition.gate == ()
    assert partition.warn == ()
    assert partition.info == ()


# --- render_git_conventions_gate_prompt ---------------------------------------


def test_render_git_conventions_gate_prompt_empty_bucket_returns_empty_string() -> None:
    partition = sg.partition_git_conventions([])

    assert sg.render_git_conventions_gate_prompt(partition) == ""


def test_render_git_conventions_gate_prompt_single_block_includes_message_and_severity() -> None:
    f = _finding("BLOCK", "abc12345: forbidden trailer matched pattern 'Co-Authored-By: .+'")
    partition = sg.partition_git_conventions([f])

    rendered = sg.render_git_conventions_gate_prompt(partition)

    assert "abc12345: forbidden trailer matched pattern" in rendered
    assert "[BLOCK]" in rendered
    assert "Resolve by amending" in rendered


def test_render_git_conventions_gate_prompt_high_finding_shows_high_severity() -> None:
    f = _finding("HIGH", "abc12345: subject exceeds 72 chars (got 80)")
    partition = sg.partition_git_conventions([f])

    rendered = sg.render_git_conventions_gate_prompt(partition)

    assert "abc12345: subject exceeds 72 chars (got 80)" in rendered
    assert "[HIGH]" in rendered


def test_render_git_conventions_gate_prompt_multiple_findings_one_line_each() -> None:
    f1 = _finding("BLOCK", "aaaaaaaa: forbidden trailer")
    f2 = _finding("HIGH", "bbbbbbbb: subject exceeds 72 chars (got 90)")
    f3 = _finding("BLOCK", "cccccccc: another trailer hit")
    partition = sg.partition_git_conventions([f1, f2, f3])

    rendered = sg.render_git_conventions_gate_prompt(partition)

    # One bullet per finding; the "Resolve by amending..." footer appears once.
    lines = rendered.splitlines()
    bullet_lines = [line for line in lines if line.startswith("- ")]
    assert len(bullet_lines) == 3
    assert rendered.count("Resolve by amending") == 1


def test_render_git_conventions_gate_prompt_includes_header_line() -> None:
    f = _finding("BLOCK", "abc12345: forbidden trailer")
    partition = sg.partition_git_conventions([f])

    rendered = sg.render_git_conventions_gate_prompt(partition)

    # The header announces the gate-bucket contents so the operator knows
    # exactly which class of violation is blocking ship.
    assert "git-convention" in rendered.lower()


# --- render_git_conventions_warn_summary --------------------------------------


def test_render_git_conventions_warn_summary_empty_bucket_returns_empty_string() -> None:
    partition = sg.partition_git_conventions([])

    assert sg.render_git_conventions_warn_summary(partition) == ""


def test_render_git_conventions_warn_summary_single_medium_renders_one_line() -> None:
    f = _finding("MEDIUM", "abc12345: subject capitalization deviates from convention")
    partition = sg.partition_git_conventions([f])

    rendered = sg.render_git_conventions_warn_summary(partition)

    bullet_lines = [line for line in rendered.splitlines() if line.startswith("- ")]
    assert len(bullet_lines) == 1
    assert "abc12345: subject capitalization" in rendered


def test_render_git_conventions_warn_summary_multiple_findings_one_line_each() -> None:
    f1 = _finding("MEDIUM", "aaaaaaaa: subject capitalization")
    f2 = _finding("MEDIUM", "bbbbbbbb: trailer order non-canonical")
    partition = sg.partition_git_conventions([f1, f2])

    rendered = sg.render_git_conventions_warn_summary(partition)

    bullet_lines = [line for line in rendered.splitlines() if line.startswith("- ")]
    assert len(bullet_lines) == 2

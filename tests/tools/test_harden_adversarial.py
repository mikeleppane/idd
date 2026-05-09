"""Tests for `tools.harden.adversarial` — capped red-team probe dispatch."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from tools.harden.adversarial import (
    AdversarialAttempt,
    AdversarialBudget,
    AdversarialResult,
    run_adversarial,
)
from tools.harden.contract import HardenError


def _write_spec(repo_root: Path, feature_id: str) -> Path:
    feature_dir = repo_root / ".forge" / "features" / feature_id
    feature_dir.mkdir(parents=True, exist_ok=True)
    target = feature_dir / "SPEC.md"
    target.write_text("# Spec\n\nstub spec\n", encoding="utf-8")
    return target


def _stepping_clock(start: float = 0.0, step: float = 0.1) -> Iterator[float]:
    current = start
    while True:
        yield current
        current += step


def _make_clock(start: float = 0.0, step: float = 0.1) -> Callable[[], float]:
    iterator = _stepping_clock(start=start, step=step)

    def _clock() -> float:
        return next(iterator)

    return _clock


def test_adversarial_clean_run_returns_pass(tmp_path: Path) -> None:
    feature_id = "2026-05-09-adv-clean"
    _write_spec(tmp_path, feature_id)

    max_attempts = 10

    def runner(attempt_number: int) -> AdversarialAttempt:
        if attempt_number > max_attempts:
            raise StopIteration
        return AdversarialAttempt(
            attempt_id=f"attempt-{attempt_number}",
            description=f"clean scenario {attempt_number}",
            breakage_found=False,
            severity="info",
            detail="",
            walltime_seconds=0.05,
        )

    result = run_adversarial(
        tmp_path,
        feature_id,
        runner=runner,
        clock=_make_clock(),
    )

    assert isinstance(result, AdversarialResult)
    assert result.status == "pass"
    assert result.attempts == max_attempts
    assert result.breakages_found == 0
    assert result.findings == []


def test_adversarial_high_severity_breakage_returns_fail(tmp_path: Path) -> None:
    feature_id = "2026-05-09-adv-high"
    _write_spec(tmp_path, feature_id)

    def runner(attempt_number: int) -> AdversarialAttempt:
        if attempt_number == 1:
            return AdversarialAttempt(
                attempt_id="attempt-1",
                description="injection bypasses filter",
                breakage_found=True,
                severity="high",
                detail="reproduces with payload X",
                walltime_seconds=0.2,
            )
        raise StopIteration

    result = run_adversarial(
        tmp_path,
        feature_id,
        runner=runner,
        clock=_make_clock(),
    )

    assert result.status == "fail"
    assert result.attempts == 1
    assert result.breakages_found == 1
    assert len(result.findings) == 1
    assert result.findings[0].severity == "high"
    assert result.findings[0].attempt_id == "attempt-1"


def test_adversarial_low_severity_breakage_returns_partial(tmp_path: Path) -> None:
    feature_id = "2026-05-09-adv-medium"
    _write_spec(tmp_path, feature_id)

    def runner(attempt_number: int) -> AdversarialAttempt:
        if attempt_number == 1:
            return AdversarialAttempt(
                attempt_id="attempt-1",
                description="ui glitches under unicode",
                breakage_found=True,
                severity="medium",
                detail="layout shift only",
                walltime_seconds=0.2,
            )
        raise StopIteration

    result = run_adversarial(
        tmp_path,
        feature_id,
        runner=runner,
        clock=_make_clock(),
    )

    assert result.status == "partial"
    assert result.attempts == 1
    assert result.breakages_found == 1
    assert result.findings[0].severity == "medium"


def test_adversarial_attempt_cap_enforced(tmp_path: Path) -> None:
    feature_id = "2026-05-09-adv-attempt-cap"
    _write_spec(tmp_path, feature_id)

    cap = 5

    def runner(attempt_number: int) -> AdversarialAttempt:
        # never raises StopIteration — cap must terminate the loop.
        return AdversarialAttempt(
            attempt_id=f"attempt-{attempt_number}",
            description="endless probe",
            breakage_found=False,
            severity="info",
            detail="",
            walltime_seconds=0.01,
        )

    budget = AdversarialBudget(max_attempts=cap, max_walltime_minutes=10)
    result = run_adversarial(
        tmp_path,
        feature_id,
        runner=runner,
        budget=budget,
        clock=_make_clock(step=0.001),
    )

    assert result.attempts == cap
    # cap hit before runner signaled completion → partial
    assert result.status == "partial"


def test_adversarial_walltime_cap_enforced(tmp_path: Path) -> None:
    feature_id = "2026-05-09-adv-walltime-cap"
    _write_spec(tmp_path, feature_id)

    def runner(attempt_number: int) -> AdversarialAttempt:
        return AdversarialAttempt(
            attempt_id=f"attempt-{attempt_number}",
            description="slow probe",
            breakage_found=False,
            severity="info",
            detail="",
            walltime_seconds=20.0,
        )

    # 1-minute walltime budget, clock advances 30s per call → cap after 2 attempts.
    budget = AdversarialBudget(max_attempts=50, max_walltime_minutes=1)
    result = run_adversarial(
        tmp_path,
        feature_id,
        runner=runner,
        budget=budget,
        clock=_make_clock(start=0.0, step=30.0),
    )

    assert result.attempts <= 3
    assert result.status == "partial"
    assert result.walltime_seconds >= 60.0


def test_adversarial_default_runner_returns_partial(tmp_path: Path) -> None:
    feature_id = "2026-05-09-adv-default"
    _write_spec(tmp_path, feature_id)

    result = run_adversarial(tmp_path, feature_id, clock=_make_clock())

    assert result.status == "partial"
    assert result.attempts == 1
    assert result.breakages_found == 0
    assert result.findings == []


def test_adversarial_missing_spec_raises(tmp_path: Path) -> None:
    feature_id = "2026-05-09-adv-no-spec"
    # No SPEC.md created.
    with pytest.raises(HardenError, match=r"SPEC\.md missing"):
        run_adversarial(tmp_path, feature_id, clock=_make_clock())


def test_adversarial_findings_filtered_to_breakages(tmp_path: Path) -> None:
    feature_id = "2026-05-09-adv-mixed"
    _write_spec(tmp_path, feature_id)

    plan: list[AdversarialAttempt] = [
        AdversarialAttempt(
            attempt_id="attempt-1",
            description="clean run",
            breakage_found=False,
            severity="info",
            detail="",
            walltime_seconds=0.1,
        ),
        AdversarialAttempt(
            attempt_id="attempt-2",
            description="found xss",
            breakage_found=True,
            severity="high",
            detail="reflected in body",
            walltime_seconds=0.3,
        ),
        AdversarialAttempt(
            attempt_id="attempt-3",
            description="another clean",
            breakage_found=False,
            severity="info",
            detail="",
            walltime_seconds=0.1,
        ),
        AdversarialAttempt(
            attempt_id="attempt-4",
            description="layout glitch",
            breakage_found=True,
            severity="low",
            detail="cosmetic only",
            walltime_seconds=0.2,
        ),
    ]

    def runner(attempt_number: int) -> AdversarialAttempt:
        if attempt_number > len(plan):
            raise StopIteration
        return plan[attempt_number - 1]

    result = run_adversarial(
        tmp_path,
        feature_id,
        runner=runner,
        clock=_make_clock(),
    )

    assert result.attempts == 4
    assert result.breakages_found == 2
    assert [f.attempt_id for f in result.findings] == ["attempt-2", "attempt-4"]
    # high severity present → fail.
    assert result.status == "fail"


def test_adversarial_custom_budget_respected(tmp_path: Path) -> None:
    feature_id = "2026-05-09-adv-custom-budget"
    _write_spec(tmp_path, feature_id)

    seen: list[int] = []

    def runner(attempt_number: int) -> AdversarialAttempt:
        seen.append(attempt_number)
        return AdversarialAttempt(
            attempt_id=f"attempt-{attempt_number}",
            description="probe",
            breakage_found=False,
            severity="info",
            detail="",
            walltime_seconds=0.0,
        )

    budget = AdversarialBudget(max_attempts=3, max_walltime_minutes=1)
    result = run_adversarial(
        tmp_path,
        feature_id,
        runner=runner,
        budget=budget,
        clock=_make_clock(step=0.001),
    )

    assert seen == [1, 2, 3]
    assert result.attempts == 3
    assert result.status == "partial"  # cap hit, runner never signaled completion


def test_adversarial_runner_stop_iteration_terminates_loop(tmp_path: Path) -> None:
    feature_id = "2026-05-09-adv-stop"
    _write_spec(tmp_path, feature_id)

    def runner(attempt_number: int) -> AdversarialAttempt:
        if attempt_number > 2:
            raise StopIteration
        return AdversarialAttempt(
            attempt_id=f"attempt-{attempt_number}",
            description="probe",
            breakage_found=False,
            severity="info",
            detail="",
            walltime_seconds=0.05,
        )

    result = run_adversarial(
        tmp_path,
        feature_id,
        runner=runner,
        clock=_make_clock(),
    )

    assert result.attempts == 2
    assert result.status == "pass"

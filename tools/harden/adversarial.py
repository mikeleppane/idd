"""Adversarial probe module for `/forge:harden`.

Coordinates a capped red-team subagent dispatch and folds per-attempt
outcomes into a structured :class:`AdversarialResult`. The actual
subagent invocation lives in the harden orchestrator skill — this module
applies the cap policy (5 minutes walltime, 50 attempted-breakage
scenarios by default) and aggregates results from an injected runner.

The runner is a callable taking the next attempt-number and returning an
:class:`AdversarialAttempt`. To signal "no more scenarios to try" the
runner raises :class:`StopIteration`. The loop also terminates when the
attempt count or accumulated walltime reaches the configured budget.

Default runner returns a single ``info`` attempt so the module remains
importable and exercisable in tests without a real subagent backend.

Reuses :class:`HardenError` from :mod:`tools.harden.contract` so harden
modules surface a single error type.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

from tools.harden.contract import HardenError
from tools.validate._frontmatter import _read_text

DEFAULT_WALLTIME_BUDGET_MIN: Final[int] = 5
DEFAULT_MAX_ATTEMPTS: Final[int] = 50

AdversarialStatus = Literal["pass", "fail", "partial"]
AttemptSeverity = Literal["high", "medium", "low", "info"]

# Detail returned by the no-op runner so callers can distinguish a
# genuinely-clean probe from an unconfigured harden run.
_DEFAULT_RUNNER_DESCRIPTION: Final[str] = "no adversarial runner configured"


@dataclass(frozen=True)
class AdversarialAttempt:
    """A single probe attempt outcome.

    Attributes:
        attempt_id: Canonical id (``attempt-1``, ``attempt-2``, ...) derived
            from the order the attempt was dispatched.
        description: Short scenario description supplied by the runner.
        breakage_found: ``True`` when the runner surfaced a real defect.
        severity: ``high`` / ``medium`` / ``low`` for breakages; ``info``
            when no breakage was found (severity is meaningful only when
            ``breakage_found`` is ``True``).
        detail: One-line observed-behavior summary or reproducer hint.
        walltime_seconds: Elapsed wall-clock time the attempt consumed.
    """

    attempt_id: str
    description: str
    breakage_found: bool
    severity: AttemptSeverity
    detail: str
    walltime_seconds: float


@dataclass(frozen=True)
class AdversarialBudget:
    """Cap policy for the probe loop.

    Attributes:
        max_walltime_minutes: Hard ceiling on accumulated wall-clock time.
            The loop stops once elapsed seconds reach this value.
        max_attempts: Hard ceiling on dispatched attempts. The loop stops
            once this many attempts have completed.
    """

    max_walltime_minutes: int = DEFAULT_WALLTIME_BUDGET_MIN
    max_attempts: int = DEFAULT_MAX_ATTEMPTS


@dataclass(frozen=True)
class AdversarialResult:
    """Aggregate result of a capped probe loop.

    Attributes:
        status: ``pass`` when the probe completed naturally with zero
            breakages and a real runner was used; ``fail`` when at least
            one breakage was reported with ``high`` severity; ``partial``
            otherwise (medium/low breakages, cap-truncated loops, or
            default-runner runs).
        walltime_seconds: Accumulated wall-clock time across all attempts.
        attempts: Number of attempts dispatched.
        breakages_found: Count of attempts where ``breakage_found`` is
            ``True``.
        findings: Attempts whose ``breakage_found`` is ``True``, in
            dispatch order. Clean attempts are not included to keep the
            harden record focused on actionable findings.
    """

    status: AdversarialStatus
    walltime_seconds: float
    attempts: int
    breakages_found: int
    findings: list[AdversarialAttempt] = field(default_factory=list)


def _default_runner(attempt_number: int) -> AdversarialAttempt:
    """Return a single ``info`` attempt — no real runner wired in.

    Keeps the module importable and exercisable without a subagent
    backend. The caller treats default-runner runs as ``partial`` so the
    harden record never claims confidence the probe did not earn.
    """
    return AdversarialAttempt(
        attempt_id=f"attempt-{attempt_number - 1}",
        description=_DEFAULT_RUNNER_DESCRIPTION,
        breakage_found=False,
        severity="info",
        detail="",
        walltime_seconds=0.0,
    )


def _aggregate(
    *,
    findings: list[AdversarialAttempt],
    runner_completed: bool,
    used_default_runner: bool,
) -> AdversarialStatus:
    """Fold per-attempt outcomes into the probe-level status.

    - ``fail`` when any finding has ``high`` severity.
    - ``partial`` when any breakage at medium-or-lower severity, or the
      cap truncated the loop, or the default runner was used.
    - ``pass`` only when a real runner reported zero breakages and signalled
      completion before any cap was hit.
    """
    if any(finding.severity == "high" for finding in findings):
        return "fail"
    if findings:
        return "partial"
    if used_default_runner or not runner_completed:
        return "partial"
    return "pass"


def run_adversarial(
    repo_root: Path,
    feature_id: str,
    *,
    runner: Callable[[int], AdversarialAttempt] | None = None,
    budget: AdversarialBudget | None = None,
    clock: Callable[[], float] | None = None,
) -> AdversarialResult:
    """Dispatch a capped red-team probe against a feature.

    Args:
        repo_root: Repository root the feature folder resolves under.
        feature_id: Feature identifier (e.g. ``2026-05-09-example``).
        runner: Optional callable that returns the next attempt outcome.
            Receives the 1-based attempt number. Raise ``StopIteration``
            from the runner to terminate the loop early. When omitted, a
            no-op default runner is dispatched once and the result is
            marked ``partial`` so callers know the probe was unconfigured.
        budget: Cap policy. Defaults to 5 minutes walltime and 50
            attempts.
        clock: Monotonic clock callable returning a float in seconds.
            Defaults to :func:`time.monotonic`. Tests inject a
            deterministic clock so the cap check is reproducible.

    Returns:
        :class:`AdversarialResult` with ``findings`` filtered to attempts
        where ``breakage_found`` is ``True``. ``attempts`` reflects every
        dispatch (clean and breaking).

    Raises:
        HardenError: If the feature's SPEC.md is missing.
    """
    spec_path = repo_root / ".forge" / "features" / feature_id / "SPEC.md"
    spec_text = _read_text(spec_path)
    if spec_text is None:
        raise HardenError(f"SPEC.md missing for feature {feature_id!r} at {spec_path}")

    active_budget = budget if budget is not None else AdversarialBudget()
    active_clock = clock if clock is not None else time.monotonic
    used_default_runner = runner is None
    active_runner = runner if runner is not None else _default_runner

    walltime_cap_seconds = active_budget.max_walltime_minutes * 60
    start_time = active_clock()

    attempts_dispatched = 0
    findings: list[AdversarialAttempt] = []
    runner_completed = False

    if used_default_runner:
        # Single default attempt then stop — keeps the record honest about
        # the probe being unconfigured without spinning the loop.
        attempts_dispatched = 1
        active_runner(1)
        elapsed = active_clock() - start_time
        return AdversarialResult(
            status=_aggregate(
                findings=findings,
                runner_completed=False,
                used_default_runner=True,
            ),
            walltime_seconds=elapsed,
            attempts=attempts_dispatched,
            breakages_found=0,
            findings=findings,
        )

    while True:
        next_attempt = attempts_dispatched + 1
        try:
            attempt = active_runner(next_attempt)
        except StopIteration:
            runner_completed = True
            break

        attempts_dispatched = next_attempt
        if attempt.breakage_found:
            findings.append(attempt)

        if attempts_dispatched >= active_budget.max_attempts:
            break

        elapsed = active_clock() - start_time
        if elapsed >= walltime_cap_seconds:
            break

    walltime_seconds = active_clock() - start_time
    status = _aggregate(
        findings=findings,
        runner_completed=runner_completed,
        used_default_runner=False,
    )

    return AdversarialResult(
        status=status,
        walltime_seconds=walltime_seconds,
        attempts=attempts_dispatched,
        breakages_found=len(findings),
        findings=findings,
    )

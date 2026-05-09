"""Integration regression for the post-ship QA stack.

Exercises ``tools.qa.acceptance.run_acceptance``,
``tools.qa.adversarial.run_adversarial``, and
``tools.qa.nr_regrep.run_nr_regrep`` against an in-memory feature fixture
using INJECTED runners (no LLM, no subprocess), then assembles the
resulting QA.md and asserts ``tools.validate.qa_shape.validate_qa_shape``
agrees with the four-section verdict + confidence aggregation.

Three end-to-end paths are covered:

1. Happy path — every promise ``met``, three clean adversarial attempts,
   no NR violations. Assembled frontmatter ``verdict=delivers`` /
   ``confidence=high``. Validator must return zero findings.
2. Acceptance failure — one promise ``not_met``. Assembled frontmatter
   ``verdict=does-not-deliver`` / ``confidence=low``. The shape stays
   internally consistent, so the validator must still return zero
   findings; the surfaced verdict is the QA outcome, not a shape error.
3. Misreported confidence — happy runners but the assembled frontmatter
   declares ``confidence=high`` while a section reports ``partial``.
   Validator must surface BLOCK
   ``qa_shape:confidence_aggregation_mismatch``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from tools.qa import (
    AcceptanceResult,
    ArtifactDescriptor,
    PromiseCheck,
    SpecPromise,
    run_acceptance,
)
from tools.qa.adversarial import (
    AdversarialAttempt,
    AdversarialResult,
    run_adversarial,
)
from tools.qa.nr_regrep import NRResult, run_nr_regrep
from tools.validate._finding import Finding
from tools.validate.qa_shape import validate_qa_shape

AdversarialRunner = Callable[[int], AdversarialAttempt]
MonotonicClock = Callable[[], float]

FEATURE_ID = "2026-05-09-qa-integration-fixture"


_SPEC_TEXT = """\
---
id: 2026-05-09-qa-integration-fixture
status: draft
tier: focused
created: 2026-05-09
capability: qa-integration-fixture
---

# Intent

In-memory fixture exercising the post-ship QA stack against a known set of
promises with injected runners so the integration test stays hermetic and
does not depend on any LLM or subprocess.

# Scope

## In scope

- Acceptance runner happy path.
- Adversarial loop with capped attempts.
- NR re-grep against an empty repo tree.

## Out of scope (Non-goals)

- Real LLM dispatch.

# Acceptance Criteria

1. Greeting emits the expected text.
2. Exit status is zero on the documented happy invocation.

# Negative Requirements

- MUST NOT log secrets to stdout.
"""


_STATE_PAYLOAD: dict[str, object] = {
    "feature_id": FEATURE_ID,
    "tier": "focused",
    "current_phase": "qa",
    "flow_version": 3,
    "phases": {"qa": {"status": "done"}},
    "skipped": [],
    "deviations": [],
    "commits": [],
}


def _seed_feature(tmp_path: Path) -> Path:
    """Materialise SPEC.md + state.json under the canonical feature path."""
    feature_dir = tmp_path / ".forge" / "features" / FEATURE_ID
    feature_dir.mkdir(parents=True)
    (feature_dir / "SPEC.md").write_text(_SPEC_TEXT, encoding="utf-8")
    (feature_dir / "state.json").write_text(json.dumps(_STATE_PAYLOAD, indent=2), encoding="utf-8")
    return feature_dir


def _render_qa(
    *,
    verdict: str,
    confidence: str,
    acceptance_status: str,
    edge_status: str,
    adversarial_status: str,
    nr_status: str,
    promises_checked: int,
    promises_met: int,
    attempts: int,
    breakages: int,
    nrs_scanned: int,
    violations: int,
    acceptance_evidence: str,
    edge_evidence: str,
    adversarial_evidence: str,
    nr_evidence: str,
) -> str:
    """Render a QA.md body that satisfies ``templates/feature/QA.md``."""
    frontmatter = (
        "---\n"
        f"feature_id: {FEATURE_ID}\n"
        "shipped_at: 2026-05-09T10:00:00Z\n"
        "qa_at: 2026-05-09T11:00:00Z\n"
        f"verdict: {verdict}\n"
        f"confidence: {confidence}\n"
        "flow_version: 3\n"
        "---\n\n"
        "# QA Acceptance Record\n\n"
    )
    acceptance = (
        "# Acceptance\n\n"
        f"- **Status:** {acceptance_status}\n"
        f"- **Spec promises checked:** {promises_checked}\n"
        f"- **Promises met:** {promises_met}\n"
        "- **Findings:**\n"
        "  - none\n"
        f"- **Evidence:** {acceptance_evidence}\n\n"
    )
    edge = (
        "# Edge Probing\n\n"
        f"- **Status:** {edge_status}\n"
        "- **Edges probed:** 2\n"
        "- **Failures observed:** 0\n"
        "- **Findings:**\n"
        "  - none\n"
        f"- **Evidence:** {edge_evidence}\n\n"
    )
    adversarial = (
        "# Adversarial\n\n"
        f"- **Status:** {adversarial_status}\n"
        "- **Walltime budget:** 5\n"
        f"- **Attempts:** {attempts}\n"
        f"- **Breakages found:** {breakages}\n"
        "- **Findings:**\n"
        "  - none\n"
        f"- **Evidence:** {adversarial_evidence}\n\n"
    )
    nr_section = (
        "# NR Regrep\n\n"
        f"- **Status:** {nr_status}\n"
        f"- **Negative Requirements scanned:** {nrs_scanned}\n"
        f"- **Violations re-introduced:** {violations}\n"
        "- **Findings:**\n"
        "  - none\n"
        f"- **Evidence:** {nr_evidence}\n"
    )
    return frontmatter + acceptance + edge + adversarial + nr_section


def _happy_acceptance_runner(
    artifact: ArtifactDescriptor, promises: list[SpecPromise]
) -> list[PromiseCheck]:
    """Mark every promise ``met`` with a deterministic observation."""
    del artifact
    return [
        PromiseCheck(
            promise_id=promise.promise_id,
            status="met",
            observation=f"observed delivery for {promise.promise_id}",
            reproducer=None,
        )
        for promise in promises
    ]


def _failing_acceptance_runner(
    artifact: ArtifactDescriptor, promises: list[SpecPromise]
) -> list[PromiseCheck]:
    """First promise fails, the rest succeed — surfaces does-not-deliver."""
    del artifact
    out: list[PromiseCheck] = []
    for index, promise in enumerate(promises):
        if index == 0:
            out.append(
                PromiseCheck(
                    promise_id=promise.promise_id,
                    status="not_met",
                    observation="greeting did not match the documented text",
                    reproducer=None,
                )
            )
            continue
        out.append(
            PromiseCheck(
                promise_id=promise.promise_id,
                status="met",
                observation=f"observed delivery for {promise.promise_id}",
                reproducer=None,
            )
        )
    return out


def _grep_no_matches(file_path: Path, pattern: str) -> list[tuple[int, str]]:
    """Stub grepper that never finds anything — keeps NR re-grep clean."""
    del file_path, pattern
    return []


def _make_three_attempt_runner() -> AdversarialRunner:
    """Return a runner callable that yields 3 clean attempts then stops."""
    counter = {"n": 0}

    def runner(attempt_number: int) -> AdversarialAttempt:
        counter["n"] += 1
        if counter["n"] > 3:
            raise StopIteration
        return AdversarialAttempt(
            attempt_id=f"attempt-{attempt_number}",
            description=f"attempt {attempt_number}",
            breakage_found=False,
            severity="info",
            detail="",
            walltime_seconds=0.0,
        )

    return runner


def _fixed_clock() -> MonotonicClock:
    """Monotonic clock stub that ticks one second per call.

    Keeps the adversarial walltime computation deterministic without
    exercising the real ``time.monotonic``.
    """
    state = {"t": 0.0}

    def clock() -> float:
        state["t"] += 1.0
        return state["t"]

    return clock


def test_qa_integration_happy_path_delivers(tmp_path: Path) -> None:
    """Happy stack: every check passes, assembled QA.md validates clean."""
    _seed_feature(tmp_path)

    acceptance_result: AcceptanceResult = run_acceptance(
        tmp_path,
        FEATURE_ID,
        ArtifactDescriptor(kind="cli", identifier="opaque-handle"),
        runner=_happy_acceptance_runner,
    )
    adversarial_result: AdversarialResult = run_adversarial(
        tmp_path,
        FEATURE_ID,
        runner=_make_three_attempt_runner(),
        clock=_fixed_clock(),
    )
    nr_result: NRResult = run_nr_regrep(
        tmp_path,
        FEATURE_ID,
        grepper=_grep_no_matches,
    )

    assert acceptance_result.verdict == "delivers", acceptance_result
    assert acceptance_result.promises_met == acceptance_result.promises_checked
    assert adversarial_result.status == "pass", adversarial_result
    assert adversarial_result.attempts == 3
    assert nr_result.status == "pass", nr_result

    qa_path = tmp_path / ".forge" / "features" / FEATURE_ID / "QA.md"
    qa_path.write_text(
        _render_qa(
            verdict="delivers",
            confidence="high",
            acceptance_status="delivers",
            edge_status="pass",
            adversarial_status="pass",
            nr_status="pass",
            promises_checked=acceptance_result.promises_checked,
            promises_met=acceptance_result.promises_met,
            attempts=adversarial_result.attempts,
            breakages=adversarial_result.breakages_found,
            nrs_scanned=nr_result.nrs_scanned,
            violations=len(nr_result.violations),
            acceptance_evidence="transcript-acceptance",
            edge_evidence="transcript-edge",
            adversarial_evidence="transcript-adversarial",
            nr_evidence="abc1234",
        ),
        encoding="utf-8",
    )

    findings: list[Finding] = validate_qa_shape(tmp_path, FEATURE_ID)
    assert findings == [], [(f.severity, f.message) for f in findings]


def test_qa_integration_acceptance_failure_does_not_deliver(tmp_path: Path) -> None:
    """One acceptance failure surfaces as does-not-deliver with low confidence.

    The QA.md remains shape-consistent — frontmatter mirrors the
    aggregated outcome — so the validator must still return zero
    findings. The QA RECORD flags the failure; ``qa_shape`` only audits
    structural agreement.
    """
    _seed_feature(tmp_path)

    acceptance_result: AcceptanceResult = run_acceptance(
        tmp_path,
        FEATURE_ID,
        ArtifactDescriptor(kind="cli", identifier="opaque-handle"),
        runner=_failing_acceptance_runner,
    )
    adversarial_result: AdversarialResult = run_adversarial(
        tmp_path,
        FEATURE_ID,
        runner=_make_three_attempt_runner(),
        clock=_fixed_clock(),
    )
    nr_result: NRResult = run_nr_regrep(
        tmp_path,
        FEATURE_ID,
        grepper=_grep_no_matches,
    )

    assert acceptance_result.verdict == "does-not-deliver", acceptance_result
    assert acceptance_result.promises_met < acceptance_result.promises_checked

    qa_path = tmp_path / ".forge" / "features" / FEATURE_ID / "QA.md"
    qa_path.write_text(
        _render_qa(
            verdict="does-not-deliver",
            confidence="low",
            acceptance_status="does-not-deliver",
            edge_status="pass",
            adversarial_status=adversarial_result.status,
            nr_status=nr_result.status,
            promises_checked=acceptance_result.promises_checked,
            promises_met=acceptance_result.promises_met,
            attempts=adversarial_result.attempts,
            breakages=adversarial_result.breakages_found,
            nrs_scanned=nr_result.nrs_scanned,
            violations=len(nr_result.violations),
            acceptance_evidence="transcript-acceptance",
            edge_evidence="transcript-edge",
            adversarial_evidence="transcript-adversarial",
            nr_evidence="abc1234",
        ),
        encoding="utf-8",
    )

    findings: list[Finding] = validate_qa_shape(tmp_path, FEATURE_ID)
    assert findings == [], [(f.severity, f.message) for f in findings]


def test_qa_integration_misreported_confidence_blocks(tmp_path: Path) -> None:
    """Frontmatter lies about confidence; validator must catch the mismatch."""
    _seed_feature(tmp_path)

    acceptance_result: AcceptanceResult = run_acceptance(
        tmp_path,
        FEATURE_ID,
        ArtifactDescriptor(kind="cli", identifier="opaque-handle"),
        runner=_happy_acceptance_runner,
    )
    adversarial_result: AdversarialResult = run_adversarial(
        tmp_path,
        FEATURE_ID,
        runner=_make_three_attempt_runner(),
        clock=_fixed_clock(),
    )
    nr_result: NRResult = run_nr_regrep(
        tmp_path,
        FEATURE_ID,
        grepper=_grep_no_matches,
    )

    qa_path = tmp_path / ".forge" / "features" / FEATURE_ID / "QA.md"
    # Edge Probing reports ``partial`` while frontmatter declares ``high``.
    # Computed confidence is ``partial`` (one section partial, none failing),
    # which mismatches the declared ``high``.
    qa_path.write_text(
        _render_qa(
            verdict="delivers",
            confidence="high",
            acceptance_status="delivers",
            edge_status="partial",
            adversarial_status="pass",
            nr_status="pass",
            promises_checked=acceptance_result.promises_checked,
            promises_met=acceptance_result.promises_met,
            attempts=adversarial_result.attempts,
            breakages=adversarial_result.breakages_found,
            nrs_scanned=nr_result.nrs_scanned,
            violations=len(nr_result.violations),
            acceptance_evidence="transcript-acceptance",
            edge_evidence="transcript-edge",
            adversarial_evidence="transcript-adversarial",
            nr_evidence="abc1234",
        ),
        encoding="utf-8",
    )

    findings: list[Finding] = validate_qa_shape(tmp_path, FEATURE_ID)
    blocks = [f for f in findings if f.severity == "BLOCK"]
    codes = {f.message.split(" ", 1)[0].rstrip(":—-") for f in blocks}
    assert "qa_shape:confidence_aggregation_mismatch" in codes, [
        (f.severity, f.message) for f in findings
    ]

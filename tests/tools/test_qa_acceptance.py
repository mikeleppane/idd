"""Tests for `tools.qa.acceptance` — ecosystem-agnostic black-box acceptance check."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.qa import QAError
from tools.qa.acceptance import (
    AcceptanceResult,
    ArtifactDescriptor,
    PromiseCheck,
    SpecPromise,
    parse_spec_promises,
    run_acceptance,
)


def _write_spec(repo_root: Path, feature_id: str, body: str) -> Path:
    feature_dir = repo_root / ".forge" / "features" / feature_id
    feature_dir.mkdir(parents=True, exist_ok=True)
    target = feature_dir / "SPEC.md"
    target.write_text(body, encoding="utf-8")
    return target


SPEC_FULL = """\
# Intent

The shipped artifact must let a fresh user verify a SPEC promise without internal context.
This is the second sentence of the intent paragraph.

# Scope

## In scope

- thing one
- thing two

# Scenarios

## Scenario: User runs the artifact and sees a help message

Given the artifact is installed
When the user runs --help
Then a usage line is printed.

## Scenario: User runs against a missing input

Given a missing input
When the user invokes the artifact
Then a clear error is shown.

# Acceptance Criteria

1. Help output names every documented flag.
2. Missing input yields a non-zero exit code.
3. Successful runs print no warnings on stderr.

# Negative Requirements

- MUST NOT print debug logs by default.
"""


def test_parse_spec_promises_extracts_acceptance_criteria() -> None:
    promises = parse_spec_promises(SPEC_FULL)
    acceptance = [p for p in promises if p.source == "acceptance"]
    assert len(acceptance) == 3
    assert acceptance[0].promise_id == "AC-1"
    assert acceptance[1].promise_id == "AC-2"
    assert acceptance[2].promise_id == "AC-3"
    assert "Help output" in acceptance[0].text
    assert "non-zero exit" in acceptance[1].text


def test_parse_spec_promises_extracts_scenarios() -> None:
    promises = parse_spec_promises(SPEC_FULL)
    scenarios = [p for p in promises if p.source == "scenario"]
    assert len(scenarios) == 2
    assert scenarios[0].promise_id == "scenario-1"
    assert scenarios[1].promise_id == "scenario-2"
    assert "help message" in scenarios[0].text
    assert "missing input" in scenarios[1].text


def test_parse_spec_promises_extracts_intent_first_paragraph() -> None:
    promises = parse_spec_promises(SPEC_FULL)
    intent = [p for p in promises if p.source == "intent"]
    assert len(intent) == 1
    assert intent[0].promise_id == "intent-1"
    # First paragraph only — both sentences of the first paragraph included.
    assert "fresh user" in intent[0].text
    assert "second sentence" in intent[0].text


def test_parse_spec_promises_fence_aware() -> None:
    spec = """\
# Intent

Real intent paragraph.

# Acceptance Criteria

1. Real criterion.

# Notes

```markdown
## Scenario: Fake fenced scenario should be ignored

1. Fake fenced criterion that should be ignored.
```
"""
    promises = parse_spec_promises(spec)
    # Only the real intent + 1 real AC; fenced scenario / fenced AC are ignored.
    sources = [p.source for p in promises]
    assert sources.count("scenario") == 0
    assert sources.count("acceptance") == 1
    assert sources.count("intent") == 1


def _runner_all_met(
    artifact: ArtifactDescriptor, promises: list[SpecPromise]
) -> list[PromiseCheck]:
    return [
        PromiseCheck(
            promise_id=p.promise_id,
            status="met",
            observation=f"observed {p.promise_id} works",
            reproducer="run the artifact",
        )
        for p in promises
    ]


def test_run_acceptance_all_met_delivers(tmp_path: Path) -> None:
    feature_id = "2026-05-09-acc-delivers"
    _write_spec(tmp_path, feature_id, SPEC_FULL)
    artifact = ArtifactDescriptor(kind="cli", identifier="mytool --help")

    result = run_acceptance(tmp_path, feature_id, artifact, runner=_runner_all_met)

    assert isinstance(result, AcceptanceResult)
    assert result.verdict == "delivers"
    assert result.promises_checked == result.promises_met
    assert result.promises_checked == len(result.checks)
    assert all(c.status == "met" for c in result.checks)


def test_run_acceptance_one_not_met_does_not_deliver(tmp_path: Path) -> None:
    feature_id = "2026-05-09-acc-broken"
    _write_spec(tmp_path, feature_id, SPEC_FULL)
    artifact = ArtifactDescriptor(kind="cli", identifier="mytool")

    def runner(
        a: ArtifactDescriptor, promises: list[SpecPromise]
    ) -> list[PromiseCheck]:
        out: list[PromiseCheck] = []
        for index, p in enumerate(promises):
            status = "not_met" if index == 0 else "met"
            out.append(
                PromiseCheck(
                    promise_id=p.promise_id,
                    status=status,  # type: ignore[arg-type]
                    observation=f"{p.promise_id} {status}",
                    reproducer=None,
                )
            )
        return out

    result = run_acceptance(tmp_path, feature_id, artifact, runner=runner)

    assert result.verdict == "does_not_deliver"
    assert result.promises_met < result.promises_checked


def test_run_acceptance_partial_with_skipped_returns_partial(tmp_path: Path) -> None:
    feature_id = "2026-05-09-acc-partial"
    _write_spec(tmp_path, feature_id, SPEC_FULL)
    artifact = ArtifactDescriptor(kind="library", identifier="myproject.core")

    def runner(
        a: ArtifactDescriptor, promises: list[SpecPromise]
    ) -> list[PromiseCheck]:
        statuses = ["met", "partial", "skipped"]
        out: list[PromiseCheck] = []
        for index, p in enumerate(promises):
            out.append(
                PromiseCheck(
                    promise_id=p.promise_id,
                    status=statuses[index % len(statuses)],  # type: ignore[arg-type]
                    observation=p.promise_id,
                    reproducer=None,
                )
            )
        return out

    result = run_acceptance(tmp_path, feature_id, artifact, runner=runner)

    assert result.verdict == "partial"
    assert result.promises_met >= 1
    assert result.promises_met < result.promises_checked


def test_run_acceptance_default_runner_skips_all(tmp_path: Path) -> None:
    feature_id = "2026-05-09-acc-default"
    _write_spec(tmp_path, feature_id, SPEC_FULL)
    artifact = ArtifactDescriptor(kind="other", identifier="anything")

    result = run_acceptance(tmp_path, feature_id, artifact)

    # All promises skipped → no `met`, no `not_met` → partial verdict.
    assert all(c.status == "skipped" for c in result.checks)
    assert result.promises_met == 0
    assert result.verdict == "partial"
    assert all("no acceptance runner configured" in c.observation for c in result.checks)


def test_run_acceptance_missing_spec_raises(tmp_path: Path) -> None:
    artifact = ArtifactDescriptor(kind="cli", identifier="anything")
    with pytest.raises(QAError):
        run_acceptance(tmp_path, "2026-05-09-no-spec", artifact)


def test_run_acceptance_runner_missing_promise_id_marked_skipped(tmp_path: Path) -> None:
    feature_id = "2026-05-09-acc-incomplete-runner"
    _write_spec(tmp_path, feature_id, SPEC_FULL)
    artifact = ArtifactDescriptor(kind="cli", identifier="mytool")

    def partial_runner(
        a: ArtifactDescriptor, promises: list[SpecPromise]
    ) -> list[PromiseCheck]:
        # Only return results for the first two promises; rest must be auto-skipped.
        return [
            PromiseCheck(
                promise_id=promises[0].promise_id,
                status="met",
                observation="ok",
                reproducer=None,
            ),
            PromiseCheck(
                promise_id=promises[1].promise_id,
                status="met",
                observation="ok",
                reproducer=None,
            ),
        ]

    result = run_acceptance(tmp_path, feature_id, artifact, runner=partial_runner)

    skipped = [c for c in result.checks if c.status == "skipped"]
    assert len(skipped) >= 1
    assert all("runner returned no result" in c.observation for c in skipped)
    # No `not_met` returned, but skipped are present → partial.
    assert result.verdict == "partial"


def test_run_acceptance_artifact_descriptor_passed_to_runner_unchanged(
    tmp_path: Path,
) -> None:
    feature_id = "2026-05-09-acc-passthrough"
    _write_spec(tmp_path, feature_id, SPEC_FULL)
    artifact = ArtifactDescriptor(
        kind="service",
        identifier="http://localhost:8080",
        notes="staging instance",
    )

    seen: list[ArtifactDescriptor] = []

    def runner(
        a: ArtifactDescriptor, promises: list[SpecPromise]
    ) -> list[PromiseCheck]:
        seen.append(a)
        return [
            PromiseCheck(
                promise_id=p.promise_id,
                status="met",
                observation="ok",
                reproducer=None,
            )
            for p in promises
        ]

    run_acceptance(tmp_path, feature_id, artifact, runner=runner)

    assert seen == [artifact]
    # Identity, not just equality — the exact instance is forwarded.
    assert seen[0] is artifact


def test_artifact_descriptor_supports_arbitrary_kinds() -> None:
    for kind in ("cli", "library", "service", "ui", "other"):
        descriptor = ArtifactDescriptor(kind=kind, identifier="opaque-id")  # type: ignore[arg-type]
        assert descriptor.kind == kind
        assert descriptor.identifier == "opaque-id"
        assert descriptor.notes == ""


def test_run_acceptance_check_order_matches_promise_order(tmp_path: Path) -> None:
    feature_id = "2026-05-09-acc-order"
    _write_spec(tmp_path, feature_id, SPEC_FULL)
    artifact = ArtifactDescriptor(kind="cli", identifier="mytool")

    def runner(
        a: ArtifactDescriptor, promises: list[SpecPromise]
    ) -> list[PromiseCheck]:
        # Return in REVERSE order — module must reorder to input promise order.
        return [
            PromiseCheck(
                promise_id=p.promise_id,
                status="met",
                observation="ok",
                reproducer=None,
            )
            for p in reversed(promises)
        ]

    result = run_acceptance(tmp_path, feature_id, artifact, runner=runner)
    expected_ids = [p.promise_id for p in parse_spec_promises(SPEC_FULL)]
    actual_ids = [c.promise_id for c in result.checks]
    assert actual_ids == expected_ids

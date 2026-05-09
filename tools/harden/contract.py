"""Contract-verification module for `/forge:harden`.

Re-runs the SPEC.md `# Scenarios` against the merged artifact and returns a
structured :class:`ContractResult` aggregating per-scenario outcomes.

The module deliberately exposes an injectable ``runner`` so this layer stays
pure-Python and unit-testable. The real runner (subprocess for CLI features,
``pytest`` invocation with ``@pytest.mark.contract`` for libraries) is wired up
by the harden orchestrator skill — this module just parses the SPEC, dispatches
each :class:`ScenarioPlan`, and folds outcomes into a :class:`ContractResult`.

Default runner returns ``skipped`` for every scenario so the module remains
importable and exercisable in tests without a real execution backend.

Scenario parsing is fence-aware: ``_strip_code`` is applied to the
``# Scenarios`` body before scanning for headers, so fake ``## Scenario:``
lines inside fenced code blocks (illustrations, examples) are not picked up.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

from tools.validate._frontmatter import _read_text, _strip_code

ContractStatus = Literal["pass", "fail", "partial"]
ScenarioStatus = Literal["pass", "fail", "skipped"]

# Match the `# Scenarios` body slice. Mirrors the regex in
# `tools.validate.spec_semantic` but the slice here is fed through
# `_strip_code` before header scanning, so fenced code blocks are erased.
_SCENARIOS_BLOCK = re.compile(r"(?ms)^# Scenarios\b[^\n]*\n(?P<body>.*?)(?=^# |\Z)")

# Scenario header forms accepted: `## Scenario: <title>` or `### Scenario: <title>`.
# Anchored to start-of-line; case-sensitive on `Scenario` to match the template.
_SCENARIO_HEADER = re.compile(
    r"^(?P<level>#{2,3})\s+Scenario:\s*(?P<title>.+?)\s*$",
    re.MULTILINE,
)

# Default detail returned by the no-op runner so callers can distinguish a
# genuinely-skipped scenario from an unconfigured harden run.
_DEFAULT_RUNNER_DETAIL: Final[str] = "no contract runner configured"


class HardenError(RuntimeError):
    """Raised when a harden module cannot proceed (missing inputs, malformed state)."""


@dataclass(frozen=True)
class ScenarioPlan:
    """A single scenario the runner is asked to execute.

    Attributes:
        scenario_id: Canonical id (``scenario-1``, ``scenario-2``, ...) derived
            from the order the scenario appears in SPEC.md.
        title: Human-readable title parsed from the ``Scenario: <title>`` line.
        gherkin_body: Raw text between this scenario header and the next one
            (or the end of the Scenarios section). May be empty.
        feature_id: Feature id the scenario belongs to. Lets the runner resolve
            the merged-artifact path without re-threading it from the caller.
    """

    scenario_id: str
    title: str
    gherkin_body: str
    feature_id: str


@dataclass(frozen=True)
class ScenarioOutcome:
    """Outcome of a single scenario execution.

    Attributes:
        scenario_id: Canonical id (matches the input :class:`ScenarioPlan`).
        title: Human-readable title (matches the input :class:`ScenarioPlan`).
        status: ``pass`` / ``fail`` / ``skipped``.
        detail: One-line explanation. Empty string when ``status == "pass"``.
    """

    scenario_id: str
    title: str
    status: ScenarioStatus
    detail: str


@dataclass(frozen=True)
class ContractResult:
    """Aggregate result of re-running every SPEC scenario.

    Attributes:
        status: ``pass`` when every scenario passed; ``fail`` when at least one
            scenario failed; ``partial`` when at least one scenario was skipped
            and zero failed.
        scenarios_run: Number of scenarios dispatched to the runner.
        scenarios_passed: Number of scenarios whose outcome was ``pass``.
        outcomes: Per-scenario outcomes, in the same order they appear in
            SPEC.md.
    """

    status: ContractStatus
    scenarios_run: int
    scenarios_passed: int
    outcomes: list[ScenarioOutcome] = field(default_factory=list)


def _default_runner(plan: ScenarioPlan) -> ScenarioOutcome:
    """Return a ``skipped`` outcome — no real backend wired in.

    Keeps the module importable and exercisable without a contract executor
    (subprocess for CLI features, pytest marker invocation for libraries).
    Real wiring lives in the harden orchestrator skill.
    """
    return ScenarioOutcome(
        scenario_id=plan.scenario_id,
        title=plan.title,
        status="skipped",
        detail=_DEFAULT_RUNNER_DETAIL,
    )


def _parse_scenario_plans(spec_text: str, feature_id: str) -> list[ScenarioPlan]:
    """Parse the ``# Scenarios`` section into ordered :class:`ScenarioPlan` records.

    Fence-aware: ``_strip_code`` is applied to the section body so fenced code
    blocks (which the SPEC template uses for Gherkin illustrations) cannot
    smuggle ``## Scenario:`` headers into the parse.
    """
    block_match = _SCENARIOS_BLOCK.search(spec_text)
    if block_match is None:
        return []

    stripped_body = _strip_code(block_match.group("body"))

    headers = list(_SCENARIO_HEADER.finditer(stripped_body))
    plans: list[ScenarioPlan] = []
    for index, header in enumerate(headers):
        title = header.group("title").strip()
        body_start = header.end()
        body_end = headers[index + 1].start() if index + 1 < len(headers) else len(stripped_body)
        plans.append(
            ScenarioPlan(
                scenario_id=f"scenario-{index + 1}",
                title=title,
                gherkin_body=stripped_body[body_start:body_end].strip("\n"),
                feature_id=feature_id,
            )
        )
    return plans


def _aggregate(outcomes: list[ScenarioOutcome]) -> ContractStatus:
    """Fold per-scenario statuses into the contract-level status.

    - ``pass`` — every outcome is ``pass`` (also the empty-list case: nothing
      to fail or skip means nothing breaks the contract).
    - ``fail`` — at least one outcome is ``fail``.
    - ``partial`` — no failures, at least one skip.
    """
    if any(outcome.status == "fail" for outcome in outcomes):
        return "fail"
    if any(outcome.status == "skipped" for outcome in outcomes):
        return "partial"
    return "pass"


def run_contract(
    repo_root: Path,
    feature_id: str,
    *,
    runner: Callable[[ScenarioPlan], ScenarioOutcome] | None = None,
) -> ContractResult:
    """Re-run scenarios from `.forge/features/<feature_id>/SPEC.md`.

    Args:
        repo_root: Repository root the feature folder resolves under.
        feature_id: Feature identifier (e.g. ``2026-05-09-example``).
        runner: Optional callable that executes a single scenario. When
            omitted, the default runner returns ``skipped`` for every scenario
            so the module stays importable without a real backend.

    Returns:
        :class:`ContractResult` with per-scenario outcomes preserved in
        SPEC source order.

    Raises:
        HardenError: If the feature's SPEC.md is missing.
    """
    spec_path = repo_root / ".forge" / "features" / feature_id / "SPEC.md"
    spec_text = _read_text(spec_path)
    if spec_text is None:
        raise HardenError(f"SPEC.md missing for feature {feature_id!r} at {spec_path}")

    plans = _parse_scenario_plans(spec_text, feature_id)
    active_runner = runner if runner is not None else _default_runner

    outcomes = [active_runner(plan) for plan in plans]
    scenarios_passed = sum(1 for outcome in outcomes if outcome.status == "pass")

    return ContractResult(
        status=_aggregate(outcomes),
        scenarios_run=len(outcomes),
        scenarios_passed=scenarios_passed,
        outcomes=outcomes,
    )

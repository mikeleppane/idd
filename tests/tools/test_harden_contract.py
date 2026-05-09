"""Tests for `tools.harden.contract` — re-runs SPEC scenarios post-ship."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.harden.contract import (
    ContractResult,
    HardenError,
    ScenarioOutcome,
    ScenarioPlan,
    ScenarioStatus,
    run_contract,
)


def _write_feature(
    repo_root: Path,
    feature_id: str,
    spec_text: str | None,
) -> Path:
    feature_dir = repo_root / ".forge" / "features" / feature_id
    feature_dir.mkdir(parents=True, exist_ok=True)
    if spec_text is not None:
        (feature_dir / "SPEC.md").write_text(spec_text, encoding="utf-8")
    return feature_dir


def _spec_with_scenarios(*titles: str) -> str:
    body = "# Scenarios\n\n"
    for title in titles:
        body += f"## Scenario: {title}\n\nGiven setup\nWhen action\nThen outcome\n\n"
    return body


def test_run_contract_all_pass_returns_pass(tmp_path: Path) -> None:
    feature_id = "2026-05-09-contract-happy"
    _write_feature(tmp_path, feature_id, _spec_with_scenarios("alpha", "beta"))

    def runner(plan: ScenarioPlan) -> ScenarioOutcome:
        return ScenarioOutcome(
            scenario_id=plan.scenario_id,
            title=plan.title,
            status="pass",
            detail="",
        )

    result = run_contract(tmp_path, feature_id, runner=runner)

    assert isinstance(result, ContractResult)
    assert result.status == "pass"
    assert result.scenarios_run == 2
    assert result.scenarios_passed == 2
    assert [outcome.status for outcome in result.outcomes] == ["pass", "pass"]


def test_run_contract_one_fail_returns_fail(tmp_path: Path) -> None:
    feature_id = "2026-05-09-contract-mixed"
    _write_feature(tmp_path, feature_id, _spec_with_scenarios("alpha", "beta", "gamma"))

    def runner(plan: ScenarioPlan) -> ScenarioOutcome:
        status: ScenarioStatus = "fail" if plan.title == "beta" else "pass"
        return ScenarioOutcome(
            scenario_id=plan.scenario_id,
            title=plan.title,
            status=status,
            detail="boom" if status == "fail" else "",
        )

    result = run_contract(tmp_path, feature_id, runner=runner)

    assert result.status == "fail"
    assert result.scenarios_run == 3
    assert result.scenarios_passed == 2


def test_run_contract_skipped_only_returns_partial(tmp_path: Path) -> None:
    feature_id = "2026-05-09-contract-skipped"
    _write_feature(tmp_path, feature_id, _spec_with_scenarios("alpha", "beta"))

    def runner(plan: ScenarioPlan) -> ScenarioOutcome:
        return ScenarioOutcome(
            scenario_id=plan.scenario_id,
            title=plan.title,
            status="skipped",
            detail="not wired",
        )

    result = run_contract(tmp_path, feature_id, runner=runner)

    assert result.status == "partial"
    assert result.scenarios_run == 2
    assert result.scenarios_passed == 0


def test_run_contract_default_runner_skips(tmp_path: Path) -> None:
    feature_id = "2026-05-09-contract-default"
    _write_feature(tmp_path, feature_id, _spec_with_scenarios("alpha", "beta"))

    result = run_contract(tmp_path, feature_id)

    assert result.status == "partial"
    assert result.scenarios_run == 2
    assert result.scenarios_passed == 0
    for outcome in result.outcomes:
        assert outcome.status == "skipped"
        assert outcome.detail == "no contract runner configured"


def test_run_contract_missing_spec_raises(tmp_path: Path) -> None:
    feature_id = "2026-05-09-contract-no-spec"
    # Feature directory exists but SPEC.md absent.
    (tmp_path / ".forge" / "features" / feature_id).mkdir(parents=True)

    with pytest.raises(HardenError, match=r"SPEC\.md missing"):
        run_contract(tmp_path, feature_id)


def test_run_contract_fence_aware_parse(tmp_path: Path) -> None:
    feature_id = "2026-05-09-contract-fence"
    spec_text = (
        "# Scenarios\n\n"
        "## Scenario: real one\n\n"
        "Given setup\nWhen action\nThen outcome\n\n"
        "Example fenced block — must NOT be parsed:\n\n"
        "```markdown\n"
        "## Scenario: fake one inside fence\n"
        "Given fake\nWhen fake\nThen fake\n"
        "```\n"
    )
    _write_feature(tmp_path, feature_id, spec_text)

    captured: list[str] = []

    def runner(plan: ScenarioPlan) -> ScenarioOutcome:
        captured.append(plan.title)
        return ScenarioOutcome(
            scenario_id=plan.scenario_id,
            title=plan.title,
            status="pass",
            detail="",
        )

    result = run_contract(tmp_path, feature_id, runner=runner)

    assert captured == ["real one"]
    assert result.scenarios_run == 1


def test_run_contract_outcomes_preserve_input_order(tmp_path: Path) -> None:
    feature_id = "2026-05-09-contract-order"
    _write_feature(
        tmp_path,
        feature_id,
        _spec_with_scenarios("first", "second", "third"),
    )

    def runner(plan: ScenarioPlan) -> ScenarioOutcome:
        return ScenarioOutcome(
            scenario_id=plan.scenario_id,
            title=plan.title,
            status="pass",
            detail="",
        )

    result = run_contract(tmp_path, feature_id, runner=runner)

    assert [outcome.title for outcome in result.outcomes] == ["first", "second", "third"]
    assert [outcome.scenario_id for outcome in result.outcomes] == [
        "scenario-1",
        "scenario-2",
        "scenario-3",
    ]


def test_run_contract_no_scenarios_returns_empty_pass(tmp_path: Path) -> None:
    feature_id = "2026-05-09-contract-empty"
    _write_feature(tmp_path, feature_id, "# Scenarios\n\n_None yet._\n")

    result = run_contract(tmp_path, feature_id)

    assert result.status == "pass"
    assert result.scenarios_run == 0
    assert result.scenarios_passed == 0
    assert result.outcomes == []

"""Tests for the dispatch_brief convention check in hooks/check_budget."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "hooks" / "check_budget.py"


def _load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_budget", HOOK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_budget = _load_hook()


_VALID_BUDGET_PROMPT_BODY = (
    "context_budget:\n"
    "{\n"
    '  "files_in_scope": ["tools/state.py"],\n'
    '  "forbidden": ["read entire repo"]\n'
    "}\n"
)


def _build_rule(
    *,
    rule_id: str = "agents-md-mandatory-skills",
    pattern_kind: str = "required_text",
    pattern: str = "coding-guidance-python",
    scope: list[str] | None = None,
    severity: str = "HIGH",
    source_file: str = "AGENTS.md",
    source_line: int = 75,
) -> dict[str, Any]:
    return {
        "id": rule_id,
        "source_file": source_file,
        "source_line": source_line,
        "pattern_kind": pattern_kind,
        "pattern": pattern,
        "scope": scope if scope is not None else ["dispatch_brief"],
        "severity": severity,
    }


def _write_conventions(repo_root: Path, rules: list[dict[str, Any]] | str) -> None:
    forge_dir = repo_root / ".forge"
    forge_dir.mkdir(parents=True, exist_ok=True)
    path = forge_dir / "conventions.json"
    if isinstance(rules, str):
        path.write_text(rules, encoding="utf-8")
    else:
        path.write_text(json.dumps(rules), encoding="utf-8")


# ---------------------------------------------------------------------------
# No-op paths
# ---------------------------------------------------------------------------


def test_check_dispatch_brief_conventions_allows_when_no_forge_directory(tmp_path: Path) -> None:
    allow, reason = check_budget._check_dispatch_brief_conventions("any prompt", repo_root=tmp_path)
    assert allow
    assert reason == "ok"


def test_check_dispatch_brief_conventions_allows_when_conventions_file_absent(
    tmp_path: Path,
) -> None:
    (tmp_path / ".forge").mkdir()
    allow, reason = check_budget._check_dispatch_brief_conventions("any prompt", repo_root=tmp_path)
    assert allow
    assert reason == "ok"


def test_check_dispatch_brief_conventions_allows_when_rule_list_empty(tmp_path: Path) -> None:
    _write_conventions(tmp_path, [])
    allow, reason = check_budget._check_dispatch_brief_conventions("any prompt", repo_root=tmp_path)
    assert allow
    assert reason == "ok"


def test_check_dispatch_brief_conventions_allows_when_no_rules_target_dispatch_brief(
    tmp_path: Path,
) -> None:
    rules = [
        _build_rule(
            rule_id="commit-only-rule",
            pattern_kind="forbidden_text",
            pattern="Co-Authored-By: Claude",
            scope=["commit_body"],
            severity="HIGH",
        ),
        _build_rule(
            rule_id="diff-only-rule",
            pattern_kind="filename_glob_forbidden",
            pattern="**/*.pyc",
            scope=["diff"],
            severity="BLOCK",
        ),
    ]
    _write_conventions(tmp_path, rules)
    allow, reason = check_budget._check_dispatch_brief_conventions(
        "Co-Authored-By: Claude in the body", repo_root=tmp_path
    )
    assert allow
    assert reason == "ok"


# ---------------------------------------------------------------------------
# Schema-broken / malformed file
# ---------------------------------------------------------------------------


def test_check_dispatch_brief_conventions_denies_when_conventions_json_malformed(
    tmp_path: Path,
) -> None:
    """Fail-closed on broken conventions.json.

    Previous behavior allowed silently — a developer could disable the
    dispatch_brief gate by introducing a JSON parse error. The hook now
    denies with an explanatory reason pointing at the validator CLI.
    """
    _write_conventions(tmp_path, "{not valid json")
    allow, reason = check_budget._check_dispatch_brief_conventions(
        "Co-Authored-By: Claude", repo_root=tmp_path
    )
    assert not allow
    assert "conventions.json present but invalid" in reason


def test_check_dispatch_brief_conventions_denies_when_schema_invalid_rule(tmp_path: Path) -> None:
    """Fail-closed on structurally invalid rule shape (e.g. unknown severity)."""
    rule = _build_rule()
    rule["severity"] = "MEGA-BLOCK"  # not in enum
    _write_conventions(tmp_path, [rule])
    allow, reason = check_budget._check_dispatch_brief_conventions(
        "no required text here", repo_root=tmp_path
    )
    assert not allow
    assert "conventions.json present but invalid" in reason


# ---------------------------------------------------------------------------
# forbidden_text + dispatch_brief
# ---------------------------------------------------------------------------


def test_check_dispatch_brief_conventions_denies_on_forbidden_text_high(tmp_path: Path) -> None:
    rules = [
        _build_rule(
            rule_id="no-claude-coauthor",
            pattern_kind="forbidden_text",
            pattern="Co-Authored-By: Claude.*",
            scope=["dispatch_brief"],
            severity="HIGH",
        ),
    ]
    _write_conventions(tmp_path, rules)
    allow, reason = check_budget._check_dispatch_brief_conventions(
        "please add Co-Authored-By: Claude <noreply@anthropic.com>", repo_root=tmp_path
    )
    assert not allow
    assert "no-claude-coauthor" in reason
    assert "forbidden_text" in reason


def test_check_dispatch_brief_conventions_allows_when_forbidden_text_absent(tmp_path: Path) -> None:
    rules = [
        _build_rule(
            rule_id="no-claude-coauthor",
            pattern_kind="forbidden_text",
            pattern="Co-Authored-By: Claude.*",
            scope=["dispatch_brief"],
            severity="HIGH",
        ),
    ]
    _write_conventions(tmp_path, rules)
    allow, reason = check_budget._check_dispatch_brief_conventions(
        "clean prompt with no banned phrase", repo_root=tmp_path
    )
    assert allow
    assert reason == "ok"


@pytest.mark.parametrize("severity", ["MEDIUM", "LOW", "WARN"])
def test_check_dispatch_brief_conventions_allows_on_non_blocking_severity(
    tmp_path: Path,
    severity: str,
) -> None:
    rules = [
        _build_rule(
            rule_id="no-claude-coauthor",
            pattern_kind="forbidden_text",
            pattern="Co-Authored-By: Claude.*",
            scope=["dispatch_brief"],
            severity=severity,
        ),
    ]
    _write_conventions(tmp_path, rules)
    allow, _reason = check_budget._check_dispatch_brief_conventions(
        "please add Co-Authored-By: Claude <noreply@anthropic.com>", repo_root=tmp_path
    )
    assert allow


def test_check_dispatch_brief_conventions_denies_on_block_severity(tmp_path: Path) -> None:
    rules = [
        _build_rule(
            rule_id="no-claude-coauthor",
            pattern_kind="forbidden_text",
            pattern="Co-Authored-By: Claude.*",
            scope=["dispatch_brief"],
            severity="BLOCK",
        ),
    ]
    _write_conventions(tmp_path, rules)
    allow, reason = check_budget._check_dispatch_brief_conventions(
        "please add Co-Authored-By: Claude <noreply@anthropic.com>", repo_root=tmp_path
    )
    assert not allow
    assert "no-claude-coauthor" in reason


# ---------------------------------------------------------------------------
# required_text + dispatch_brief
# ---------------------------------------------------------------------------


def test_check_dispatch_brief_conventions_denies_when_required_text_missing(
    tmp_path: Path,
) -> None:
    rules = [
        _build_rule(
            rule_id="must-cite-coding-guidance",
            pattern_kind="required_text",
            pattern="coding-guidance-python",
            scope=["dispatch_brief"],
            severity="HIGH",
        ),
    ]
    _write_conventions(tmp_path, rules)
    allow, reason = check_budget._check_dispatch_brief_conventions(
        "this prompt forgets to cite the skill", repo_root=tmp_path
    )
    assert not allow
    assert "must-cite-coding-guidance" in reason
    assert "required_text not found in dispatch_brief" in reason


def test_check_dispatch_brief_conventions_allows_when_required_text_present(
    tmp_path: Path,
) -> None:
    rules = [
        _build_rule(
            rule_id="must-cite-coding-guidance",
            pattern_kind="required_text",
            pattern="coding-guidance-python",
            scope=["dispatch_brief"],
            severity="HIGH",
        ),
    ]
    _write_conventions(tmp_path, rules)
    allow, reason = check_budget._check_dispatch_brief_conventions(
        "cite coding-guidance-python in this brief", repo_root=tmp_path
    )
    assert allow
    assert reason == "ok"


# ---------------------------------------------------------------------------
# Mis-scoped filename_glob_forbidden — load_conventions raises, hook allows
# ---------------------------------------------------------------------------


def test_check_dispatch_brief_conventions_allows_when_filename_glob_misscoped_dispatch_brief(
    tmp_path: Path,
) -> None:
    rules = [
        _build_rule(
            rule_id="misscoped-glob",
            pattern_kind="filename_glob_forbidden",
            pattern="**/*.pyc",
            scope=["dispatch_brief"],
            severity="BLOCK",
        ),
    ]
    _write_conventions(tmp_path, rules)
    allow, reason = check_budget._check_dispatch_brief_conventions("any prompt", repo_root=tmp_path)
    assert allow
    assert reason == "ok"


# ---------------------------------------------------------------------------
# Multi-rule precedence
# ---------------------------------------------------------------------------


def test_check_dispatch_brief_conventions_first_fired_rule_in_lex_order_wins(
    tmp_path: Path,
) -> None:
    rules = [
        _build_rule(
            rule_id="z-late-rule",
            pattern_kind="forbidden_text",
            pattern="banned-phrase",
            scope=["dispatch_brief"],
            severity="HIGH",
        ),
        _build_rule(
            rule_id="a-early-rule",
            pattern_kind="forbidden_text",
            pattern="banned-phrase",
            scope=["dispatch_brief"],
            severity="HIGH",
        ),
    ]
    _write_conventions(tmp_path, rules)
    allow, reason = check_budget._check_dispatch_brief_conventions(
        "contains banned-phrase here", repo_root=tmp_path
    )
    assert not allow
    assert reason.startswith("a-early-rule")


# ---------------------------------------------------------------------------
# Integration with the existing checks (evaluate -> short-circuit)
# ---------------------------------------------------------------------------


def test_evaluate_denies_with_budget_reason_when_budget_block_missing_even_if_rules_would_fire(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rules = [
        _build_rule(
            rule_id="must-cite-coding-guidance",
            pattern_kind="required_text",
            pattern="coding-guidance-python",
            scope=["dispatch_brief"],
            severity="HIGH",
        ),
    ]
    _write_conventions(tmp_path, rules)
    monkeypatch.chdir(tmp_path)
    allow, reason = check_budget.evaluate("prompt with no budget block at all")
    assert not allow
    assert "missing required" in reason
    assert "must-cite-coding-guidance" not in reason


def test_evaluate_denies_with_conventions_reason_when_budget_valid_and_rule_fires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rules = [
        _build_rule(
            rule_id="must-cite-coding-guidance",
            pattern_kind="required_text",
            pattern="coding-guidance-python",
            scope=["dispatch_brief"],
            severity="HIGH",
        ),
    ]
    _write_conventions(tmp_path, rules)
    monkeypatch.chdir(tmp_path)
    allow, reason = check_budget.evaluate(_VALID_BUDGET_PROMPT_BODY)
    assert not allow
    assert "must-cite-coding-guidance" in reason


def test_evaluate_allows_when_budget_valid_and_required_text_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rules = [
        _build_rule(
            rule_id="must-cite-coding-guidance",
            pattern_kind="required_text",
            pattern="coding-guidance-python",
            scope=["dispatch_brief"],
            severity="HIGH",
        ),
    ]
    _write_conventions(tmp_path, rules)
    monkeypatch.chdir(tmp_path)
    prompt = _VALID_BUDGET_PROMPT_BODY + "\ncite coding-guidance-python here\n"
    allow, reason = check_budget.evaluate(prompt)
    assert allow, reason
    assert reason == "ok"


# ---------------------------------------------------------------------------
# main() end-to-end
# ---------------------------------------------------------------------------


def test_main_denies_agent_payload_when_dispatch_brief_rule_fires(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rules = [
        _build_rule(
            rule_id="no-claude-coauthor",
            pattern_kind="forbidden_text",
            pattern="Co-Authored-By: Claude.*",
            scope=["dispatch_brief"],
            severity="HIGH",
        ),
    ]
    _write_conventions(tmp_path, rules)
    monkeypatch.chdir(tmp_path)

    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Agent",
        "tool_input": {
            "prompt": (
                _VALID_BUDGET_PROMPT_BODY
                + "\nplease add Co-Authored-By: Claude <noreply@anthropic.com>\n"
            ),
        },
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))

    rc = check_budget.main()
    captured = capsys.readouterr()
    assert rc == 0
    output = json.loads(captured.out)
    decision = output["hookSpecificOutput"]
    assert decision["hookEventName"] == "PreToolUse"
    assert decision["permissionDecision"] == "deny"
    assert "no-claude-coauthor" in decision["permissionDecisionReason"]
    assert decision["permissionDecisionReason"].startswith("FORGE context-budget hook:")


# ---------------------------------------------------------------------------
# _locate_repo_root walk-up behavior
# ---------------------------------------------------------------------------


def test_locate_repo_root_finds_ancestor_with_forge_directory(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    (tmp_path / ".forge").mkdir()
    located = check_budget._locate_repo_root(start=nested)
    assert located == tmp_path


def test_locate_repo_root_returns_none_when_no_forge_ancestor(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    located = check_budget._locate_repo_root(start=nested)
    assert located is None

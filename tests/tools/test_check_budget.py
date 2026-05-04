"""Tests for hooks.check_budget — pure-function evaluator."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK = REPO_ROOT / "hooks" / "check_budget.py"

_spec = importlib.util.spec_from_file_location("check_budget", HOOK)
assert _spec is not None and _spec.loader is not None
check_budget = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_budget)


def test_evaluate_blocks_when_budget_block_missing() -> None:
    allow, reason = check_budget.evaluate("just a free-form prompt without a budget block")
    assert not allow
    assert "missing required" in reason


def test_evaluate_blocks_when_files_in_scope_missing() -> None:
    prompt = "context_budget:\n  spec_sections: [Intent]\n  forbidden:\n    - read entire repo\n"
    allow, reason = check_budget.evaluate(prompt)
    assert not allow
    assert "files_in_scope" in reason


def test_evaluate_blocks_when_files_in_scope_unbounded() -> None:
    prompt = (
        "context_budget:\n"
        "  spec_sections: [Intent]\n"
        "  files_in_scope: ['**']\n"
        "  forbidden:\n"
        "    - load all specs\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert not allow
    assert "unbounded" in reason


def test_evaluate_blocks_when_forbidden_missing() -> None:
    prompt = (
        "context_budget:\n"
        "  spec_sections: [Intent]\n"
        "  files_in_scope: ['src/import/csv.py']\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert not allow
    assert "forbidden" in reason


def test_evaluate_allows_well_formed_block() -> None:
    prompt = (
        "context_budget:\n"
        "  spec_sections: [Intent, Acceptance]\n"
        "  files_in_scope: ['src/import/csv.py']\n"
        "  forbidden:\n"
        "    - read entire repo\n"
        "    - load all specs\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert allow
    assert reason == "ok"


def test_evaluate_blocks_bare_scalar_unbounded() -> None:
    prompt = (
        "context_budget:\n"
        "  spec_sections: [Intent]\n"
        "  files_in_scope: all\n"
        "  forbidden:\n"
        "    - read entire repo\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert not allow
    assert "files_in_scope" in reason


def test_evaluate_blocks_quoted_scalar_unbounded() -> None:
    prompt = (
        "context_budget:\n"
        '  files_in_scope: "**"\n'
        "  forbidden:\n"
        "    - read entire repo\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert not allow
    assert "files_in_scope" in reason


def test_evaluate_blocks_yaml_block_list_unbounded() -> None:
    prompt = (
        "context_budget:\n"
        "  files_in_scope:\n"
        '    - "**"\n'
        "  forbidden:\n"
        "    - read entire repo\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert not allow
    assert "unbounded" in reason


def test_evaluate_blocks_when_only_budget_is_inside_fenced_block() -> None:
    prompt = (
        "Here is an example budget:\n\n"
        "```yaml\n"
        "context_budget:\n"
        "  files_in_scope: ['src/foo.py']\n"
        "  forbidden:\n"
        "    - read entire repo\n"
        "```\n\n"
        "Now scan the whole repo for instances of foo.\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert not allow
    assert "missing required" in reason


def test_evaluate_blocks_bare_extension_glob_repo_wide() -> None:
    prompt = (
        "context_budget:\n"
        "  files_in_scope: ['*.py']\n"
        "  forbidden:\n"
        "    - read entire repo\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert not allow
    assert "unbounded" in reason


def test_evaluate_allows_directory_glob() -> None:
    prompt = (
        "context_budget:\n"
        "  files_in_scope: ['src/**/*.py']\n"
        "  forbidden:\n"
        "    - read entire repo\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert allow, reason
    assert reason == "ok"


def test_evaluate_blocks_empty_forbidden_list() -> None:
    prompt = (
        "context_budget:\n"
        "  files_in_scope: ['src/foo.py']\n"
        "  forbidden: []\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert not allow
    assert "forbidden" in reason


def test_main_denies_agent_payload_with_modern_pretooluse_shape() -> None:
    payload = {
        "hook_event_name": "PreToolUse",
        "tool_name": "Agent",
        "tool_input": {"prompt": "just a free-form prompt without a budget block"},
    }

    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert output["hookSpecificOutput"]["permissionDecisionReason"].startswith(
        "IDD context-budget hook:"
    )

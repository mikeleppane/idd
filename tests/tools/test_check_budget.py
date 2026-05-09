"""Tests for hooks.check_budget — pure-function evaluator (JSON budget block)."""

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
    prompt = (
        "context_budget:\n"
        "{\n"
        '  "spec_sections": ["Intent"],\n'
        '  "forbidden": ["read entire repo"]\n'
        "}\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert not allow
    assert "files_in_scope" in reason


def test_evaluate_blocks_when_files_in_scope_unbounded_array() -> None:
    prompt = (
        "context_budget:\n"
        "{\n"
        '  "spec_sections": ["Intent"],\n'
        '  "files_in_scope": ["**"],\n'
        '  "forbidden": ["load all specs"]\n'
        "}\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert not allow
    assert "unbounded" in reason


def test_evaluate_blocks_when_forbidden_missing() -> None:
    prompt = (
        "context_budget:\n"
        "{\n"
        '  "spec_sections": ["Intent"],\n'
        '  "files_in_scope": ["src/import/csv.py"]\n'
        "}\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert not allow
    assert "forbidden" in reason


def test_evaluate_allows_well_formed_block() -> None:
    prompt = (
        "context_budget:\n"
        "{\n"
        '  "spec_sections": ["Intent", "Acceptance"],\n'
        '  "files_in_scope": ["src/import/csv.py"],\n'
        '  "forbidden": ["read entire repo", "load all specs"]\n'
        "}\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert allow
    assert reason == "ok"


def test_evaluate_blocks_files_in_scope_string_not_array() -> None:
    prompt = (
        'context_budget:\n{\n  "files_in_scope": "all",\n  "forbidden": ["read entire repo"]\n}\n'
    )
    allow, reason = check_budget.evaluate(prompt)
    assert not allow
    assert "files_in_scope" in reason


def test_evaluate_blocks_when_only_budget_is_inside_fenced_block() -> None:
    prompt = (
        "Here is an example budget:\n\n"
        "```json\n"
        "context_budget:\n"
        "{\n"
        '  "files_in_scope": ["src/foo.py"],\n'
        '  "forbidden": ["read entire repo"]\n'
        "}\n"
        "```\n\n"
        "Now scan the whole repo for instances of foo.\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert not allow
    assert "missing required" in reason


def test_evaluate_blocks_bare_extension_glob_repo_wide() -> None:
    prompt = (
        "context_budget:\n"
        "{\n"
        '  "files_in_scope": ["*.py"],\n'
        '  "forbidden": ["read entire repo"]\n'
        "}\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert not allow
    assert "unbounded" in reason


def test_evaluate_allows_directory_glob() -> None:
    prompt = (
        "context_budget:\n"
        "{\n"
        '  "files_in_scope": ["src/**/*.py"],\n'
        '  "forbidden": ["read entire repo"]\n'
        "}\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert allow, reason
    assert reason == "ok"


def test_evaluate_blocks_empty_forbidden_array() -> None:
    prompt = 'context_budget:\n{\n  "files_in_scope": ["src/foo.py"],\n  "forbidden": []\n}\n'
    allow, reason = check_budget.evaluate(prompt)
    assert not allow
    assert "forbidden" in reason


def test_evaluate_blocks_invalid_json() -> None:
    prompt = "context_budget:\n{ this is not valid json }\n"
    allow, reason = check_budget.evaluate(prompt)
    assert not allow
    assert "not valid JSON" in reason


def test_evaluate_allows_inline_brace_form() -> None:
    """JSON object on the same line as the marker is also accepted."""
    prompt = (
        'context_budget: {"files_in_scope": ["src/foo.py"], "forbidden": ["read entire repo"]}\n'
    )
    allow, reason = check_budget.evaluate(prompt)
    assert allow, reason
    assert reason == "ok"


def test_evaluate_blocks_junk_between_marker_and_json() -> None:
    """Reject malformed prompts with stray text between marker and the JSON object."""
    prompt = (
        "context_budget: garbage prefix\n"
        "{\n"
        '  "files_in_scope": ["src/foo.py"],\n'
        '  "forbidden": ["read entire repo"]\n'
        "}\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert not allow
    assert "missing required" in reason


def test_evaluate_allows_dispatch_with_optional_articles_field() -> None:
    """Pin the dispatch hook's permissiveness on the optional `articles[]`
    field. `hooks/check_budget.py` only enforces `files_in_scope` +
    `forbidden`; the `articles[]` budget field rides through unchanged.
    Guards against a future hook tightening that silently regresses the
    dispatch contract.
    """
    prompt = (
        "context_budget:\n"
        "{\n"
        '  "spec_sections": ["Acceptance"],\n'
        '  "files_in_scope": ["src/main.py"],\n'
        '  "forbidden": ["read entire repo"],\n'
        '  "return_format": {"max_words": 100},\n'
        '  "articles": [\n'
        "    {"
        '"id": "A1", "title": "Vault", "level": "CRITICAL", '
        '"rule": "Use vault.", "reference": null, "rationale": null'
        "}\n"
        "  ]\n"
        "}\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert allow, "permissive allow -> empty output"
    assert reason == "ok"


def test_evaluate_allows_dispatch_without_articles_field() -> None:
    """Hook must not require `articles[]` — older dispatches predate it."""
    prompt = (
        "context_budget:\n"
        "{\n"
        '  "spec_sections": ["Acceptance"],\n'
        '  "files_in_scope": ["src/main.py"],\n'
        '  "forbidden": ["read entire repo"],\n'
        '  "return_format": {"max_words": 100}\n'
        "}\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert allow
    assert reason == "ok"


def test_check_budget_execute_with_tests_in_scope_passes() -> None:
    """Execute-phase dispatch with non-empty tests_in_scope is allowed."""
    prompt = (
        "context_budget:\n"
        "{\n"
        '  "phase": "execute",\n'
        '  "files_in_scope": ["tools/state.py"],\n'
        '  "tests_in_scope": ["tests/tools/test_state.py"],\n'
        '  "forbidden": ["read entire repo"]\n'
        "}\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert allow, reason
    assert reason == "ok"


def test_check_budget_execute_missing_tests_in_scope_blocks() -> None:
    """Execute-phase dispatch missing tests_in_scope is denied."""
    prompt = (
        "context_budget:\n"
        "{\n"
        '  "phase": "execute",\n'
        '  "files_in_scope": ["tools/state.py"],\n'
        '  "forbidden": ["read entire repo"]\n'
        "}\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert not allow
    assert "tests_in_scope" in reason


def test_check_budget_execute_empty_tests_in_scope_blocks() -> None:
    """Execute-phase dispatch with empty tests_in_scope and no exception is denied."""
    prompt = (
        "context_budget:\n"
        "{\n"
        '  "phase": "execute",\n'
        '  "files_in_scope": ["tools/state.py"],\n'
        '  "tests_in_scope": [],\n'
        '  "forbidden": ["read entire repo"]\n'
        "}\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert not allow
    assert "tests_in_scope" in reason


def test_check_budget_execute_empty_tests_in_scope_with_exception_passes() -> None:
    """Empty tests_in_scope is allowed when budget block declares tdd_exception_ref."""
    prompt = (
        "context_budget:\n"
        "{\n"
        '  "phase": "execute",\n'
        '  "files_in_scope": ["tools/state.py"],\n'
        '  "tests_in_scope": [],\n'
        '  "tdd_exception_ref": "ADR-3",\n'
        '  "forbidden": ["read entire repo"]\n'
        "}\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert allow, reason
    assert reason == "ok"


def test_check_budget_non_execute_tests_in_scope_optional() -> None:
    """Non-execute (or absent) phase makes tests_in_scope optional."""
    prompt = (
        "context_budget:\n"
        "{\n"
        '  "phase": "review",\n'
        '  "files_in_scope": ["tools/state.py"],\n'
        '  "forbidden": ["read entire repo"]\n'
        "}\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert allow, reason
    assert reason == "ok"


def test_check_budget_phase_absent_tests_in_scope_optional() -> None:
    """Absent phase field is treated as non-execute; tests_in_scope optional."""
    prompt = (
        "context_budget:\n"
        "{\n"
        '  "files_in_scope": ["tools/state.py"],\n'
        '  "forbidden": ["read entire repo"]\n'
        "}\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert allow, reason
    assert reason == "ok"


def test_check_budget_execute_tests_in_scope_must_be_array() -> None:
    """tests_in_scope under execute phase must be a JSON array, not a string."""
    prompt = (
        "context_budget:\n"
        "{\n"
        '  "phase": "execute",\n'
        '  "files_in_scope": ["tools/state.py"],\n'
        '  "tests_in_scope": "tests/tools/test_state.py",\n'
        '  "forbidden": ["read entire repo"]\n'
        "}\n"
    )
    allow, reason = check_budget.evaluate(prompt)
    assert not allow
    assert "tests_in_scope" in reason


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
        "FORGE context-budget hook:"
    )

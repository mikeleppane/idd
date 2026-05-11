"""Tests for validate_conventions (.forge/conventions.json runtime)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tools import validate as validate_pkg
from tools.validate import validate_conventions
from tools.validate.conventions import (
    Convention,
    load_conventions,
    match_convention,
)


def _well_formed(**overrides: Any) -> dict[str, Any]:
    """Build a minimal well-formed convention entry."""
    base: dict[str, Any] = {
        "id": "ban-claude-coauthor",
        "source_file": "AGENTS.md",
        "source_line": 42,
        "pattern_kind": "forbidden_text",
        "pattern": r"Co-Authored-By: Claude",
        "scope": ["commit_body"],
        "severity": "BLOCK",
    }
    base.update(overrides)
    return base


def _write_conventions(repo_root: Path, entries: list[dict[str, Any]] | str) -> None:
    forge = repo_root / ".forge"
    forge.mkdir(exist_ok=True)
    target = forge / "conventions.json"
    if isinstance(entries, str):
        target.write_text(entries, encoding="utf-8")
    else:
        target.write_text(json.dumps(entries), encoding="utf-8")


# --- Schema validation -------------------------------------------------------


def test_missing_file_returns_empty_findings(tmp_path: Path) -> None:
    findings = validate_conventions(tmp_path)
    assert findings == []


def test_malformed_json_blocks_with_parse_message(tmp_path: Path) -> None:
    _write_conventions(tmp_path, "{not json")
    findings = validate_conventions(tmp_path)
    assert len(findings) == 1
    assert findings[0].severity == "BLOCK"
    assert "json" in findings[0].message.lower()


def test_empty_array_returns_empty_findings(tmp_path: Path) -> None:
    _write_conventions(tmp_path, [])
    assert validate_conventions(tmp_path) == []


def test_missing_required_field_blocks(tmp_path: Path) -> None:
    entry = _well_formed()
    del entry["pattern"]
    _write_conventions(tmp_path, [entry])
    findings = validate_conventions(tmp_path)
    assert any(f.severity == "BLOCK" and "pattern" in f.message for f in findings), findings


def test_unknown_extra_field_blocks(tmp_path: Path) -> None:
    entry = _well_formed(bogus="x")
    _write_conventions(tmp_path, [entry])
    findings = validate_conventions(tmp_path)
    assert any(f.severity == "BLOCK" for f in findings), findings


def test_severity_outside_vocabulary_blocks(tmp_path: Path) -> None:
    entry = _well_formed(severity="CRITICAL")
    _write_conventions(tmp_path, [entry])
    findings = validate_conventions(tmp_path)
    assert any(f.severity == "BLOCK" and "severity" in f.message.lower() for f in findings), (
        findings
    )


def test_empty_scope_array_blocks(tmp_path: Path) -> None:
    entry = _well_formed(scope=[])
    _write_conventions(tmp_path, [entry])
    findings = validate_conventions(tmp_path)
    assert any(f.severity == "BLOCK" and "scope" in f.message.lower() for f in findings), findings


def test_unknown_scope_value_blocks(tmp_path: Path) -> None:
    entry = _well_formed(scope=["branch_name"])
    _write_conventions(tmp_path, [entry])
    findings = validate_conventions(tmp_path)
    assert any(f.severity == "BLOCK" and "scope" in f.message.lower() for f in findings), findings


def test_unknown_pattern_kind_blocks(tmp_path: Path) -> None:
    entry = _well_formed(pattern_kind="weird_kind")
    _write_conventions(tmp_path, [entry])
    findings = validate_conventions(tmp_path)
    assert any(f.severity == "BLOCK" and "pattern_kind" in f.message for f in findings), findings


def test_source_line_zero_blocks(tmp_path: Path) -> None:
    entry = _well_formed(source_line=0)
    _write_conventions(tmp_path, [entry])
    findings = validate_conventions(tmp_path)
    assert any(f.severity == "BLOCK" and "source_line" in f.message for f in findings), findings


def test_source_line_negative_blocks(tmp_path: Path) -> None:
    entry = _well_formed(source_line=-5)
    _write_conventions(tmp_path, [entry])
    findings = validate_conventions(tmp_path)
    assert any(f.severity == "BLOCK" and "source_line" in f.message for f in findings), findings


def test_id_uppercase_blocks(tmp_path: Path) -> None:
    entry = _well_formed(id="BadID")
    _write_conventions(tmp_path, [entry])
    findings = validate_conventions(tmp_path)
    assert any(f.severity == "BLOCK" and "id" in f.message.lower() for f in findings), findings


def test_id_with_spaces_blocks(tmp_path: Path) -> None:
    entry = _well_formed(id="has spaces")
    _write_conventions(tmp_path, [entry])
    findings = validate_conventions(tmp_path)
    assert any(f.severity == "BLOCK" and "id" in f.message.lower() for f in findings), findings


def test_top_level_non_array_blocks(tmp_path: Path) -> None:
    _write_conventions(tmp_path, '{"not": "an array"}')
    findings = validate_conventions(tmp_path)
    assert any(f.severity == "BLOCK" for f in findings), findings


# --- Duplicate IDs -----------------------------------------------------------


def test_duplicate_id_blocks_naming_the_id(tmp_path: Path) -> None:
    a = _well_formed(id="rule-x")
    b = _well_formed(id="rule-x", pattern=r"other")
    _write_conventions(tmp_path, [a, b])
    findings = validate_conventions(tmp_path)
    assert any(f.severity == "BLOCK" and "rule-x" in f.message for f in findings), findings


# --- Pattern compilation -----------------------------------------------------


def test_bad_regex_pattern_blocks_with_rule_id(tmp_path: Path) -> None:
    entry = _well_formed(id="bad-regex", pattern=r"[")
    _write_conventions(tmp_path, [entry])
    findings = validate_conventions(tmp_path, commit_body="anything")
    assert any(f.severity == "BLOCK" and "bad-regex" in f.message for f in findings), findings


def test_bad_regex_in_required_text_blocks(tmp_path: Path) -> None:
    entry = _well_formed(id="bad-req", pattern_kind="required_text", pattern=r"(unclosed")
    _write_conventions(tmp_path, [entry])
    findings = validate_conventions(tmp_path, commit_body="anything")
    assert any(f.severity == "BLOCK" and "bad-req" in f.message for f in findings), findings


# --- Mis-scoped filename_glob_forbidden -------------------------------------


def test_filename_glob_forbidden_with_commit_body_scope_blocks(tmp_path: Path) -> None:
    entry = _well_formed(
        id="bad-glob-scope",
        pattern_kind="filename_glob_forbidden",
        pattern="*.env",
        scope=["commit_body"],
    )
    _write_conventions(tmp_path, [entry])
    findings = validate_conventions(tmp_path)
    assert any(f.severity == "BLOCK" and "bad-glob-scope" in f.message for f in findings), findings


def test_filename_glob_forbidden_with_diff_and_dispatch_brief_blocks(tmp_path: Path) -> None:
    entry = _well_formed(
        id="bad-glob-mix",
        pattern_kind="filename_glob_forbidden",
        pattern="*.env",
        scope=["diff", "dispatch_brief"],
    )
    _write_conventions(tmp_path, [entry])
    findings = validate_conventions(tmp_path)
    assert any(f.severity == "BLOCK" and "bad-glob-mix" in f.message for f in findings), findings


def test_filename_glob_forbidden_with_diff_only_loads_clean(tmp_path: Path) -> None:
    entry = _well_formed(
        id="ok-glob",
        pattern_kind="filename_glob_forbidden",
        pattern="*.env",
        scope=["diff"],
    )
    _write_conventions(tmp_path, [entry])
    findings = validate_conventions(tmp_path)
    assert findings == []


# --- Runtime: forbidden_text -------------------------------------------------


def test_forbidden_text_fires_when_match_in_commit_body(tmp_path: Path) -> None:
    entry = _well_formed(
        id="ban-claude-coauthor",
        pattern=r"Co-Authored-By: Claude",
        scope=["commit_body"],
        severity="BLOCK",
    )
    _write_conventions(tmp_path, [entry])
    body = "feat: x\n\nCo-Authored-By: Claude <noreply@anthropic.com>\n"
    findings = validate_conventions(tmp_path, commit_body=body)
    fired = [f for f in findings if "ban-claude-coauthor" in f.message]
    assert len(fired) == 1
    assert fired[0].severity == "BLOCK"
    assert "forbidden_text" in fired[0].message.lower()


def test_forbidden_text_silent_when_no_match(tmp_path: Path) -> None:
    entry = _well_formed(
        id="ban-claude-coauthor",
        pattern=r"Co-Authored-By: Claude",
        scope=["commit_body"],
        severity="BLOCK",
    )
    _write_conventions(tmp_path, [entry])
    findings = validate_conventions(tmp_path, commit_body="feat: ok\n")
    assert findings == []


def test_forbidden_text_skipped_when_commit_body_is_none(tmp_path: Path) -> None:
    entry = _well_formed(
        id="ban-claude-coauthor",
        pattern=r"Co-Authored-By: Claude",
        scope=["commit_body"],
        severity="BLOCK",
    )
    _write_conventions(tmp_path, [entry])
    findings = validate_conventions(tmp_path, commit_body=None)
    assert findings == []


# --- Runtime: required_text --------------------------------------------------


def test_required_text_fires_when_pattern_missing(tmp_path: Path) -> None:
    entry = _well_formed(
        id="need-signoff",
        pattern_kind="required_text",
        pattern=r"(?i)Signed-off-by:",
        scope=["commit_body"],
        severity="HIGH",
    )
    _write_conventions(tmp_path, [entry])
    findings = validate_conventions(tmp_path, commit_body="feat: x\n")
    fired = [f for f in findings if "need-signoff" in f.message]
    assert len(fired) == 1
    assert fired[0].severity == "HIGH"
    assert "required_text" in fired[0].message.lower()


def test_required_text_silent_when_pattern_found(tmp_path: Path) -> None:
    entry = _well_formed(
        id="need-signoff",
        pattern_kind="required_text",
        pattern=r"(?i)Signed-off-by:",
        scope=["commit_body"],
        severity="HIGH",
    )
    _write_conventions(tmp_path, [entry])
    findings = validate_conventions(
        tmp_path, commit_body="feat: x\n\nSigned-off-by: Alice <alice@example.com>\n"
    )
    assert findings == []


def test_required_text_skipped_when_commit_body_is_none(tmp_path: Path) -> None:
    entry = _well_formed(
        id="need-signoff",
        pattern_kind="required_text",
        pattern=r"(?i)Signed-off-by:",
        scope=["commit_body"],
        severity="HIGH",
    )
    _write_conventions(tmp_path, [entry])
    assert validate_conventions(tmp_path, commit_body=None) == []


# --- Runtime: filename_glob_forbidden ---------------------------------------


def test_filename_glob_forbidden_fires_on_new_path_match(tmp_path: Path) -> None:
    entry = _well_formed(
        id="ban-env-files",
        pattern_kind="filename_glob_forbidden",
        pattern="**/*.env*",
        scope=["diff"],
        severity="BLOCK",
    )
    _write_conventions(tmp_path, [entry])
    diff = (
        "diff --git a/configs/.env.prod b/configs/.env.prod\n"
        "--- /dev/null\n"
        "+++ b/configs/.env.prod\n"
        "@@\n"
        "+SECRET=hunter2\n"
    )
    findings = validate_conventions(tmp_path, diff=diff)
    fired = [f for f in findings if "ban-env-files" in f.message]
    assert len(fired) == 1
    assert "configs/.env.prod" in fired[0].message


def test_filename_glob_forbidden_silent_when_no_matching_new_path(tmp_path: Path) -> None:
    entry = _well_formed(
        id="ban-env-files",
        pattern_kind="filename_glob_forbidden",
        pattern="**/*.env*",
        scope=["diff"],
        severity="BLOCK",
    )
    _write_conventions(tmp_path, [entry])
    diff = "+++ b/tools/state.py\n@@\n+def helper(): ...\n"
    findings = validate_conventions(tmp_path, diff=diff)
    assert findings == []


def test_filename_glob_forbidden_skipped_when_diff_is_none(tmp_path: Path) -> None:
    entry = _well_formed(
        id="ban-env-files",
        pattern_kind="filename_glob_forbidden",
        pattern="**/*.env*",
        scope=["diff"],
        severity="BLOCK",
    )
    _write_conventions(tmp_path, [entry])
    assert validate_conventions(tmp_path, diff=None) == []


# --- Scope filtering ---------------------------------------------------------


def test_rule_does_not_fire_when_scope_not_passed(tmp_path: Path) -> None:
    """A diff-scoped rule must NOT fire when only commit_body is checked."""
    entry = _well_formed(
        id="diff-only",
        pattern_kind="forbidden_text",
        pattern=r"TODO",
        scope=["diff"],
        severity="HIGH",
    )
    _write_conventions(tmp_path, [entry])
    findings = validate_conventions(tmp_path, commit_body="contains TODO inside")
    assert findings == []


def test_dispatch_brief_scope_skipped_by_conventions_target(tmp_path: Path) -> None:
    """Rules scoped only to dispatch_brief are not checked by this target."""
    entry = _well_formed(
        id="hook-rule",
        pattern_kind="required_text",
        pattern=r"MUST cite",
        scope=["dispatch_brief"],
        severity="HIGH",
    )
    _write_conventions(tmp_path, [entry])
    findings = validate_conventions(tmp_path, commit_body="abc", diff="xyz")
    assert findings == []


# --- Determinism -------------------------------------------------------------


def test_determinism_same_inputs_same_findings(tmp_path: Path) -> None:
    entries = [
        _well_formed(id="z-rule", pattern=r"zzz"),
        _well_formed(id="a-rule", pattern=r"aaa"),
        _well_formed(id="m-rule", pattern=r"mmm"),
    ]
    _write_conventions(tmp_path, entries)
    body = "aaa zzz mmm"
    first = validate_conventions(tmp_path, commit_body=body)
    second = validate_conventions(tmp_path, commit_body=body)
    assert first == second
    # Order: rules sorted lexicographically by id.
    fired_ids = [f.message.split(":", 1)[0] for f in first]
    assert fired_ids == sorted(fired_ids)


# --- match_convention helper -------------------------------------------------


def test_match_convention_forbidden_text_hit() -> None:
    rule = Convention(
        id="r",
        source_file="x.md",
        source_line=1,
        pattern_kind="forbidden_text",
        pattern=r"BAD",
        scope=("commit_body",),
        severity="BLOCK",
    )
    assert match_convention(rule, text="contains BAD here", scope="commit_body") is True
    assert match_convention(rule, text="all good", scope="commit_body") is False


def test_match_convention_scope_filter() -> None:
    rule = Convention(
        id="r",
        source_file="x.md",
        source_line=1,
        pattern_kind="forbidden_text",
        pattern=r"BAD",
        scope=("commit_body",),
        severity="BLOCK",
    )
    # Scope mismatch → False even if the text matches the pattern.
    assert match_convention(rule, text="BAD", scope="diff") is False


def test_match_convention_required_text_miss() -> None:
    rule = Convention(
        id="r",
        source_file="x.md",
        source_line=1,
        pattern_kind="required_text",
        pattern=r"NEED",
        scope=("commit_body",),
        severity="HIGH",
    )
    assert match_convention(rule, text="lacks anchor", scope="commit_body") is True
    assert match_convention(rule, text="has NEED here", scope="commit_body") is False


def test_match_convention_filename_glob() -> None:
    rule = Convention(
        id="r",
        source_file="x.md",
        source_line=1,
        pattern_kind="filename_glob_forbidden",
        pattern="**/*.env*",
        scope=("diff",),
        severity="BLOCK",
    )
    assert match_convention(rule, text="configs/.env.prod", scope="diff") is True
    assert match_convention(rule, text="tools/state.py", scope="diff") is False


# --- load_conventions --------------------------------------------------------


def test_load_conventions_returns_typed_records(tmp_path: Path) -> None:
    entry = _well_formed()
    _write_conventions(tmp_path, [entry])
    rules = load_conventions(tmp_path)
    assert len(rules) == 1
    assert isinstance(rules[0], Convention)
    assert rules[0].id == "ban-claude-coauthor"
    assert rules[0].scope == ("commit_body",)


def test_load_conventions_missing_file_returns_empty(tmp_path: Path) -> None:
    assert load_conventions(tmp_path) == []


# --- CLI integration ---------------------------------------------------------


def test_cli_target_conventions_zero_when_file_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = validate_pkg.main(["--target", "conventions", "--repo-root", str(tmp_path)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload == {"target": "conventions", "findings": []}


def test_cli_target_conventions_blocks_on_malformed_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_conventions(tmp_path, "{nope")
    rc = validate_pkg.main(["--target", "conventions", "--repo-root", str(tmp_path)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1
    assert any(f["severity"] == "BLOCK" for f in payload["findings"])


def test_cli_target_conventions_zero_on_well_formed_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Well-formed file with only commit_body/diff rules exits 0 — CLI does not
    feed those scopes any text, so pattern-firing rules are skipped."""
    entries = [
        _well_formed(),
        _well_formed(
            id="need-signoff",
            pattern_kind="required_text",
            pattern=r"(?i)Signed-off-by:",
            scope=["commit_body"],
            severity="HIGH",
        ),
        _well_formed(
            id="ban-env",
            pattern_kind="filename_glob_forbidden",
            pattern="**/*.env*",
            scope=["diff"],
            severity="BLOCK",
        ),
    ]
    _write_conventions(tmp_path, entries)
    rc = validate_pkg.main(["--target", "conventions", "--repo-root", str(tmp_path)])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0, payload
    assert payload["findings"] == []


# --- Fixture sanity ----------------------------------------------------------


def test_sample_fixture_loads_clean(repo_root: Path, tmp_path: Path) -> None:
    sample = repo_root / "tests" / "fixtures" / "conventions" / "sample_conventions.json"
    assert sample.is_file(), f"missing sample fixture: {sample}"
    forge = tmp_path / ".forge"
    forge.mkdir()
    (forge / "conventions.json").write_text(sample.read_text(encoding="utf-8"), encoding="utf-8")
    findings = validate_conventions(tmp_path)
    assert findings == [], findings

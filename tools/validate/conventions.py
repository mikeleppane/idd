"""Pattern-based convention validator (.forge/conventions.json runtime).

Loads convention rules authored by the WS2 inventory step, validates their
shape against the JSON Schema, and matches them against commit/diff payloads
at review time. Rules scoped to ``dispatch_brief`` are intentionally not
pattern-fired here — the dispatch hook reuses :mod:`tools.conventions_runtime`
directly so it stays stdlib-only.

Architecture:

* :mod:`tools.conventions_runtime` — stdlib-only types + permissive loader +
  match engine. The dispatch hook (``hooks/check_budget.py``) imports from
  there.
* This module — schema-strict loader plus ``Finding``-shaped validator built
  on top of the runtime primitives. Schema (``jsonschema``) + ``yaml`` deps
  are pulled in via :mod:`._frontmatter`, so consumers in environments that
  lack those deps should not import this module.
"""

from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path
from typing import Any, Final

from tools._glob import globstar_match
from tools.conventions_runtime import (
    Convention,
    PatternKind,
    Scope,
    _build_one,
    has_redos_shape,
    load_conventions_permissive,
    match_convention,
)

from ._finding import Finding
from ._frontmatter import _build_validator, _load_schema

__all__ = [
    "Convention",
    "PatternKind",
    "Scope",
    "load_conventions",
    "match_convention",
    "validate_conventions",
]

_TARGET: Final[str] = "conventions"
_CONVENTIONS_FILENAME: Final[str] = "conventions.json"
_SCHEMA_FILENAME: Final[str] = "conventions.schema.json"
_MATCH_EXCERPT_MAX: Final[int] = 80

_NEW_PATH_LINE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)


def _conventions_path(repo_root: Path) -> Path:
    return repo_root / ".forge" / _CONVENTIONS_FILENAME


def _parse_payload(path: Path) -> list[Any] | Finding:
    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        return Finding("BLOCK", _TARGET, path, f"failed to parse JSON: {exc}")
    if not isinstance(payload, list):
        return Finding(
            "BLOCK",
            _TARGET,
            path,
            f"conventions root must be a JSON array, got {type(payload).__name__}",
        )
    return payload


def _schema_findings(payload: list[Any], path: Path) -> list[Finding]:
    schema = _load_schema(_SCHEMA_FILENAME)
    validator = _build_validator(schema)
    findings: list[Finding] = []
    for err in sorted(validator.iter_errors(payload), key=lambda e: list(e.path)):
        if err.path:
            location_parts = [str(part) for part in err.path]
            location = ".".join(location_parts)
            offending_field = location_parts[-1]
            findings.append(
                Finding(
                    "BLOCK",
                    _TARGET,
                    path,
                    f"{location} ({offending_field}): {err.message}",
                ),
            )
        else:
            findings.append(Finding("BLOCK", _TARGET, path, err.message))
    return findings


def _duplicate_id_findings(
    payload: list[Any],
    path: Path,
) -> list[Finding]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        rule_id = entry.get("id")
        if not isinstance(rule_id, str):
            continue
        if rule_id in seen and rule_id not in duplicates:
            duplicates.append(rule_id)
        seen.add(rule_id)
    return [
        Finding("BLOCK", _TARGET, path, f"duplicate id {dup!r}; ids must be globally unique")
        for dup in duplicates
    ]


def _filename_scope_findings(
    rules: list[Convention],
    path: Path,
) -> list[Finding]:
    """``filename_glob_forbidden`` only makes sense on the diff scope."""
    findings: list[Finding] = []
    for rule in rules:
        if rule.pattern_kind != "filename_glob_forbidden":
            continue
        if tuple(rule.scope) != ("diff",):
            findings.append(
                Finding(
                    "BLOCK",
                    _TARGET,
                    path,
                    f"{rule.id}: filename_glob_forbidden requires scope ['diff'], "
                    f"got {list(rule.scope)}",
                ),
            )
    return findings


def load_conventions(repo_root: Path) -> list[Convention]:
    """Parse ``.forge/conventions.json`` and return the typed rule list.

    Strict path: runs schema + duplicate-id + regex-compile + ReDoS-shape +
    scope shape checks. Use :func:`tools.conventions_runtime.load_conventions_permissive`
    when the stdlib-only path is required (e.g. dispatch hook).

    Args:
        repo_root: Repository root containing the ``.forge`` directory.

    Returns:
        List of :class:`Convention` records. Empty list when the file is
        absent (mirrors :func:`validate_config`).

    Raises:
        ValueError: When the file is present but fails JSON parse, schema,
            duplicate-id, regex-compile, or ReDoS-shape checks. Callers
            that need a finding-shaped response use :func:`validate_conventions`.
    """
    path = _conventions_path(repo_root)
    if not path.is_file():
        return []
    parsed = _parse_payload(path)
    if isinstance(parsed, Finding):
        raise ValueError(parsed.message)
    schema_errors = _schema_findings(parsed, path)
    if schema_errors:
        raise ValueError("; ".join(f.message for f in schema_errors))
    rules = load_conventions_permissive(repo_root)
    # Regex compile + ReDoS-shape pre-check so callers (e.g. amend lifecycle)
    # never have to repeat the work. The permissive loader skips this so the
    # dispatch hook can still match rules with valid patterns when the file
    # also carries a broken one — but the strict path treats any compile
    # failure as a fatal load error.
    for rule in rules:
        if rule.pattern_kind == "filename_glob_forbidden":
            continue
        try:
            re.compile(rule.pattern)
        except re.error as exc:
            raise ValueError(
                f"{rule.id}: failed to compile pattern {rule.pattern!r}: {exc}",
            ) from exc
        if has_redos_shape(rule.pattern):
            raise ValueError(
                f"{rule.id}: pattern carries an obvious catastrophic-backtracking shape; "
                "rewrite without nested unbounded quantifiers",
            )
    scope_errors = _filename_scope_findings(rules, path)
    if scope_errors:
        raise ValueError("; ".join(f.message for f in scope_errors))
    return rules


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def _check_forbidden_text(
    rule: Convention,
    *,
    compiled: re.Pattern[str],
    text: str,
    path: Path,
) -> list[Finding]:
    match = compiled.search(text)
    if match is None:
        return []
    excerpt = _truncate(match.group(0), _MATCH_EXCERPT_MAX)
    return [
        Finding(
            rule.severity,
            _TARGET,
            path,
            f"{rule.id}: forbidden_text matched: {excerpt}",
        ),
    ]


def _check_required_text(
    rule: Convention,
    *,
    compiled: re.Pattern[str],
    text: str,
    scope: Scope,
    path: Path,
) -> list[Finding]:
    if compiled.search(text) is not None:
        return []
    return [
        Finding(
            rule.severity,
            _TARGET,
            path,
            f"{rule.id}: required_text not found in {scope}",
        ),
    ]


def _check_text_scope(
    rule: Convention,
    *,
    text: str,
    scope: Scope,
    path: Path,
) -> list[Finding]:
    """Apply a regex-based rule against ``text`` for ``scope``; emit findings."""
    if scope not in rule.scope:
        return []
    if rule.pattern_kind == "filename_glob_forbidden":
        return _check_filename_scope(rule, diff=text, path=path)
    try:
        compiled = re.compile(rule.pattern)
    except re.error:
        return [
            Finding(
                "BLOCK",
                _TARGET,
                path,
                f"{rule.id}: failed to compile pattern {rule.pattern!r}",
            ),
        ]
    if rule.pattern_kind == "forbidden_text":
        return _check_forbidden_text(rule, compiled=compiled, text=text, path=path)
    return _check_required_text(rule, compiled=compiled, text=text, scope=scope, path=path)


def _check_filename_scope(
    rule: Convention,
    *,
    diff: str,
    path: Path,
) -> list[Finding]:
    """Match ``filename_glob_forbidden`` against ``+++ b/<path>`` lines in a diff."""
    if "diff" not in rule.scope:
        return []
    findings: list[Finding] = []
    seen_paths: set[str] = set()
    for match in _NEW_PATH_LINE.finditer(diff):
        new_path = match.group(1).strip()
        if new_path in seen_paths:
            continue
        seen_paths.add(new_path)
        if "**" in rule.pattern:
            hit = globstar_match(new_path, rule.pattern)
        else:
            hit = fnmatch.fnmatch(new_path, rule.pattern)
        if hit:
            findings.append(
                Finding(
                    rule.severity,
                    _TARGET,
                    path,
                    f"{rule.id}: forbidden filename: {new_path}",
                ),
            )
    return findings


def _check_pattern_compile(rule: Convention, path: Path) -> Finding | None:
    """Surface a regex compile / ReDoS-shape error so the rule is not silently dropped."""
    if rule.pattern_kind == "filename_glob_forbidden":
        return None
    try:
        re.compile(rule.pattern)
    except re.error:
        return Finding(
            "BLOCK",
            _TARGET,
            path,
            f"{rule.id}: failed to compile pattern {rule.pattern!r}",
        )
    if has_redos_shape(rule.pattern):
        return Finding(
            "BLOCK",
            _TARGET,
            path,
            f"{rule.id}: pattern carries an obvious catastrophic-backtracking shape",
        )
    return None


def _build_rules_from_payload(payload: list[Any]) -> list[Convention]:
    """Pump the schema-validated payload through the runtime builder.

    Keeps the strict path on the same dataclass surface the permissive
    runtime emits — no duplicate validation, no re-implementation of the
    typed conversion. The schema phase has already rejected non-dict
    entries by the time this runs.
    """
    rules: list[Convention] = []
    for idx, entry in enumerate(payload):
        if not isinstance(entry, dict):
            continue
        rules.append(_build_one(idx, entry))
    return rules


def validate_conventions(
    repo_root: Path,
    *,
    commit_body: str | None = None,
    diff: str | None = None,
) -> list[Finding]:
    """Validate ``.forge/conventions.json`` and run pattern rules against inputs.

    Shape validation always runs: schema, duplicate id, regex compile,
    ReDoS-shape, mis-scoped ``filename_glob_forbidden``. Pattern-firing checks
    are gated on the corresponding input being non-None — passing
    ``commit_body=None`` (the CLI default) skips ``commit_body``-scope rules
    entirely; the same rule applies to ``diff``. ``dispatch_brief``-scope
    rules are never pattern-fired here; shape validation still runs.

    Args:
        repo_root: Repository root containing the ``.forge`` directory.
        commit_body: Optional commit message body. ``None`` (default) skips
            ``commit_body``-scope checks; ``""`` is treated as a real input.
        diff: Optional unified diff. ``None`` (default) skips ``diff``-scope
            checks; ``""`` is treated as a real input.

    Returns:
        List of :class:`Finding` records, sorted by rule id for determinism.
        Empty list when the conventions file is absent.
    """
    path = _conventions_path(repo_root)
    if not path.is_file():
        return []
    parsed = _parse_payload(path)
    if isinstance(parsed, Finding):
        return [parsed]

    schema_errors = _schema_findings(parsed, path)
    if schema_errors:
        return schema_errors

    dup_errors = _duplicate_id_findings(parsed, path)
    if dup_errors:
        return dup_errors

    rules = sorted(_build_rules_from_payload(parsed), key=lambda r: r.id)

    findings: list[Finding] = []
    findings.extend(_filename_scope_findings(rules, path))
    for rule in rules:
        compile_err = _check_pattern_compile(rule, path)
        if compile_err is not None:
            findings.append(compile_err)
    if findings:
        return findings

    runtime: list[Finding] = []
    for rule in rules:
        if commit_body is not None:
            runtime.extend(
                _check_text_scope(rule, text=commit_body, scope="commit_body", path=path),
            )
        if diff is not None:
            runtime.extend(_check_text_scope(rule, text=diff, scope="diff", path=path))
    return runtime

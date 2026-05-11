"""Pattern-based convention validator (.forge/conventions.json runtime).

Loads convention rules authored by the WS2 inventory step, validates their
shape against the JSON Schema, and matches them against commit/diff payloads
at review time. Rules scoped to ``dispatch_brief`` are intentionally not run
here — the dispatch hook reuses ``load_conventions`` + ``match_convention``
to enforce those at PreToolUse time.
"""

from __future__ import annotations

import fnmatch
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal, cast

from tools.redaction import _globstar_match

from ._finding import Finding, Severity
from ._frontmatter import _build_validator, _load_schema

PatternKind = Literal["forbidden_text", "required_text", "filename_glob_forbidden"]
Scope = Literal["commit_body", "diff", "dispatch_brief"]

_TARGET: Final[str] = "conventions"
_CONVENTIONS_FILENAME: Final[str] = "conventions.json"
_SCHEMA_FILENAME: Final[str] = "conventions.schema.json"
_MATCH_EXCERPT_MAX: Final[int] = 80

_NEW_PATH_LINE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)


@dataclass(frozen=True, kw_only=True)
class Convention:
    """One pattern-based convention rule from ``.forge/conventions.json``.

    Attributes:
        id: Globally unique rule identifier.
        source_file: Repo-relative path of the authoring document
            (e.g. ``AGENTS.md``).
        source_line: 1-based line number in ``source_file`` where the rule
            originates; used to point operators at the authoring location.
        pattern_kind: Match-engine selector. ``forbidden_text`` / ``required_text``
            interpret ``pattern`` as a Python regex; ``filename_glob_forbidden``
            interprets ``pattern`` as a POSIX-style glob.
        pattern: The regex or glob source string.
        scope: Tuple of scopes this rule applies to. Immutable + hashable so
            ``Convention`` stays usable as a dict key or set member.
        severity: Severity emitted on a match; one of
            ``BLOCK | HIGH | MEDIUM | LOW | WARN``.
    """

    id: str
    source_file: str
    source_line: int
    pattern_kind: PatternKind
    pattern: str
    scope: tuple[Scope, ...]
    severity: Severity


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
        # Build a human-readable location pointer + name the offending field.
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


def _build_rules(payload: list[dict[str, Any]]) -> list[Convention]:
    """Map a schema-validated payload onto the typed ``Convention`` tuple list.

    ``payload`` has already cleared JSON Schema, so each entry's keys carry the
    documented types. ``cast`` narrows what mypy sees without re-validating.
    """
    rules: list[Convention] = []
    for entry in payload:
        scope_values = cast("Iterable[str]", entry["scope"])
        rules.append(
            Convention(
                id=cast("str", entry["id"]),
                source_file=cast("str", entry["source_file"]),
                source_line=cast("int", entry["source_line"]),
                pattern_kind=cast("PatternKind", entry["pattern_kind"]),
                pattern=cast("str", entry["pattern"]),
                scope=tuple(cast("Scope", s) for s in scope_values),
                severity=cast("Severity", entry["severity"]),
            ),
        )
    return rules


def load_conventions(repo_root: Path) -> list[Convention]:
    """Parse ``.forge/conventions.json`` and return the typed rule list.

    Args:
        repo_root: Repository root containing the ``.forge`` directory.

    Returns:
        List of :class:`Convention` records. Empty list when the file is
        absent (mirrors :func:`validate_config`).

    Raises:
        ValueError: When the file is present but fails JSON parse, schema,
            or duplicate-id checks. Callers that need a finding-shaped
            response use :func:`validate_conventions` instead.
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
    dup_errors = _duplicate_id_findings(parsed, path)
    if dup_errors:
        raise ValueError("; ".join(f.message for f in dup_errors))
    return _build_rules(parsed)


def _compile_or_none(pattern: str) -> re.Pattern[str] | None:
    try:
        return re.compile(pattern)
    except re.error:
        return None


def match_convention(rule: Convention, *, text: str, scope: Scope) -> bool:
    """Return ``True`` iff ``rule`` fires against ``text`` for ``scope``.

    A rule fires when (a) the requested ``scope`` is listed in ``rule.scope``
    AND (b) the pattern engine for ``rule.pattern_kind`` reports a violation:

    * ``forbidden_text`` — regex matches anywhere → True.
    * ``required_text``  — regex does NOT match anywhere → True.
    * ``filename_glob_forbidden`` — ``text`` (treated as a filename) matches
      the glob → True. Uses ``**``-aware matching for cross-segment globs;
      falls back to :func:`fnmatch.fnmatch` for plain patterns.

    Returns ``False`` when the scope filter rejects the rule, when the regex
    fails to compile (validate_conventions surfaces the compile error
    separately), or when the violation condition above does not hold.
    """
    if scope not in rule.scope:
        return False
    if rule.pattern_kind == "filename_glob_forbidden":
        if "**" in rule.pattern:
            return _globstar_match(text, rule.pattern)
        return fnmatch.fnmatch(text, rule.pattern)
    compiled = _compile_or_none(rule.pattern)
    if compiled is None:
        return False
    found = compiled.search(text) is not None
    if rule.pattern_kind == "forbidden_text":
        return found
    # required_text: violation iff NOT found.
    return not found


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
    compiled = _compile_or_none(rule.pattern)
    if compiled is None:
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
            hit = _globstar_match(new_path, rule.pattern)
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
    """Surface a regex compile error so the caller does not silently drop the rule."""
    if rule.pattern_kind == "filename_glob_forbidden":
        return None
    if _compile_or_none(rule.pattern) is not None:
        return None
    return Finding(
        "BLOCK",
        _TARGET,
        path,
        f"{rule.id}: failed to compile pattern {rule.pattern!r}",
    )


def validate_conventions(
    repo_root: Path,
    *,
    commit_body: str | None = None,
    diff: str | None = None,
) -> list[Finding]:
    """Validate ``.forge/conventions.json`` and run pattern rules against inputs.

    Shape validation always runs: schema, duplicate id, regex compile,
    mis-scoped ``filename_glob_forbidden``. Pattern-firing checks are gated
    on the corresponding input being non-None — passing ``commit_body=None``
    (the CLI default) skips ``commit_body``-scope rules entirely; the same
    rule applies to ``diff``. ``dispatch_brief``-scope rules are never run
    here (the dispatch hook reuses the helpers above).

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

    rules = sorted(_build_rules(parsed), key=lambda r: r.id)

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

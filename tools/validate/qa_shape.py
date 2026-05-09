"""QA template structural validator.

Asserts ``QA.md`` for a feature has the four required sections in order,
valid frontmatter, a verdict that matches the ``# Acceptance`` Status,
a confidence value that aggregates correctly across all four sections,
and resolvable evidence pointers when they look like paths.

The validator is a pure function. No subprocess, no I/O beyond reading
``QA.md`` + ``state.json`` inside ``.forge/features/<id>/``.

Findings (severity → code → meaning):

- ``BLOCK`` ``qa_shape:qa_md_missing``: ``state.json`` reports the qa
  phase has been marked ``done`` (flow_version=3) but ``QA.md`` is absent.
  Pre-qa flow returns no finding so authoring is not gated prematurely.
- ``BLOCK`` ``qa_shape:frontmatter_missing_key``: a required frontmatter
  key (``feature_id`` / ``shipped_at`` / ``qa_at`` / ``verdict`` /
  ``confidence`` / ``flow_version``) is absent.
- ``BLOCK`` ``qa_shape:invalid_verdict_value``: frontmatter ``verdict``
  is not one of ``delivers`` / ``partial`` / ``does-not-deliver``.
- ``BLOCK`` ``qa_shape:invalid_confidence_value``: frontmatter
  ``confidence`` is not one of ``high`` / ``partial`` / ``low``.
- ``BLOCK`` ``qa_shape:wrong_flow_version``: frontmatter
  ``flow_version`` is not ``3``.
- ``BLOCK`` ``qa_shape:section_missing``: a required H1 section
  (``# Acceptance`` / ``# Edge Probing`` / ``# Adversarial`` /
  ``# NR Regrep``) is absent.
- ``MEDIUM`` ``qa_shape:section_out_of_order``: required sections appear
  in a different order than the canonical Acceptance → Edge Probing →
  Adversarial → NR Regrep.
- ``BLOCK`` ``qa_shape:invalid_section_status``: a section's
  ``**Status:**`` value is outside the per-section allowed set.
- ``BLOCK`` ``qa_shape:verdict_mismatch``: frontmatter ``verdict`` does
  not match the ``# Acceptance`` Status verbatim.
- ``BLOCK`` ``qa_shape:confidence_aggregation_mismatch``: frontmatter
  ``confidence`` does not match the value computed from per-section
  Status fields.
- ``LOW`` ``qa_shape:evidence_path_missing``: a section's
  ``**Evidence:**`` value looks like a filesystem path but does not
  resolve under ``repo_root``. Non-path evidence (commit shas, plain
  text identifiers, http(s):// URLs) is left alone.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ._feature_layout import STATE_FILENAME
from ._finding import Finding, Severity
from ._frontmatter import _FrontmatterParseError, _parse_frontmatter, _read_text

TARGET = "qa_shape"

QA_FILENAME = "QA.md"

_REQUIRED_SECTIONS: tuple[str, ...] = (
    "Acceptance",
    "Edge Probing",
    "Adversarial",
    "NR Regrep",
)
_ACCEPTANCE_STATUSES: frozenset[str] = frozenset({"delivers", "partial", "does-not-deliver"})
_OTHER_STATUSES: frozenset[str] = frozenset({"pass", "fail", "partial", "skipped"})
_VALID_VERDICTS: frozenset[str] = frozenset({"delivers", "partial", "does-not-deliver"})
_VALID_CONFIDENCES: frozenset[str] = frozenset({"high", "partial", "low"})
_REQUIRED_FRONTMATTER_KEYS: tuple[str, ...] = (
    "feature_id",
    "shipped_at",
    "qa_at",
    "verdict",
    "confidence",
    "flow_version",
)
_EXPECTED_FLOW_VERSION = 3

_H1_RE = re.compile(r"^# (?P<name>[^\n]+?)\s*$", re.MULTILINE)
_STATUS_RE = re.compile(r"^\s*[-*]\s*\*\*Status:\*\*\s*(?P<value>\S.*?)\s*$", re.MULTILINE)
_EVIDENCE_RE = re.compile(r"^\s*[-*]\s*\*\*Evidence:\*\*\s*(?P<value>\S.*?)\s*$", re.MULTILINE)

_SEVERITY_RANK: dict[Severity, int] = {
    "BLOCK": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
    "WARN": 4,
    "INFO": 5,
}

_CODE_PREFIX = f"{TARGET}:"


def _looks_like_path(value: str) -> bool:
    """Return True iff ``value`` should be resolved as a filesystem path.

    Heuristic: leading ``/``, ``./``, or ``../``; or contains a ``/`` and
    is not a URL (``http://`` / ``https://``). Plain identifiers (commit
    shas, free-text descriptions) fall through as non-paths.
    """
    if not value:
        return False
    if value.startswith(("http://", "https://")):
        return False
    if value.startswith(("/", "./", "../")):
        return True
    return "/" in value


def _qa_phase_done(state_path: Path) -> bool:
    """Return True iff state.json marks the qa phase complete.

    Specifically: ``flow_version`` is ``3`` AND ``phases.qa.status`` is
    ``done``. Missing or malformed state returns False so absence of
    QA.md remains advisory until the qa phase has actually completed.
    """
    text = _read_text(state_path)
    if text is None:
        return False
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict) or payload.get("flow_version") != _EXPECTED_FLOW_VERSION:
        return False
    phases = payload.get("phases")
    if not isinstance(phases, dict):
        return False
    qa = phases.get("qa")
    return isinstance(qa, dict) and qa.get("status") == "done"


def _frontmatter_findings(
    fm: dict[str, object],
    qa_path: Path,
) -> list[Finding]:
    findings: list[Finding] = [
        Finding(
            "BLOCK",
            TARGET,
            qa_path,
            f"qa_shape:frontmatter_missing_key — required key {key!r} absent or empty",
            fix_hint=(f"Add `{key}: <value>` to QA.md frontmatter per templates/feature/QA.md."),
        )
        for key in _REQUIRED_FRONTMATTER_KEYS
        if key not in fm or fm[key] in (None, "")
    ]
    if findings:
        return findings

    verdict = fm.get("verdict")
    if not isinstance(verdict, str) or verdict not in _VALID_VERDICTS:
        findings.append(
            Finding(
                "BLOCK",
                TARGET,
                qa_path,
                f"qa_shape:invalid_verdict_value — verdict {verdict!r} not in "
                f"{sorted(_VALID_VERDICTS)}",
                fix_hint=(f"Set frontmatter `verdict` to one of {sorted(_VALID_VERDICTS)}."),
            )
        )

    confidence = fm.get("confidence")
    if not isinstance(confidence, str) or confidence not in _VALID_CONFIDENCES:
        findings.append(
            Finding(
                "BLOCK",
                TARGET,
                qa_path,
                f"qa_shape:invalid_confidence_value — confidence {confidence!r} "
                f"not in {sorted(_VALID_CONFIDENCES)}",
                fix_hint=(f"Set frontmatter `confidence` to one of {sorted(_VALID_CONFIDENCES)}."),
            )
        )

    flow_version = fm.get("flow_version")
    if flow_version != _EXPECTED_FLOW_VERSION:
        findings.append(
            Finding(
                "BLOCK",
                TARGET,
                qa_path,
                f"qa_shape:wrong_flow_version — flow_version is {flow_version!r}, "
                f"expected {_EXPECTED_FLOW_VERSION}",
                fix_hint=(
                    f"Set frontmatter `flow_version` to {_EXPECTED_FLOW_VERSION} "
                    f"and run state migrations if state.json uses an older flow."
                ),
            )
        )
    return findings


def _section_findings(
    body: str,
    qa_path: Path,
) -> tuple[list[Finding], dict[str, tuple[int, int]]]:
    """Return (findings, section_spans).

    section_spans maps a present section name to ``(body_start, body_end)``
    byte offsets within ``body`` covering its content (header line up to
    next H1 or end of body). Missing sections are absent from the dict.
    """
    findings: list[Finding] = []
    matches = list(_H1_RE.finditer(body))
    name_to_span: dict[str, tuple[int, int]] = {}
    seen_order: list[str] = []
    for idx, match in enumerate(matches):
        name = match.group("name").strip()
        if name not in _REQUIRED_SECTIONS:
            continue
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body)
        # Keep first occurrence span (duplicates fall through; not policy
        # we surface today, but the canonical span stays first-write).
        if name not in name_to_span:
            name_to_span[name] = (start, end)
            seen_order.append(name)

    missing = [s for s in _REQUIRED_SECTIONS if s not in name_to_span]
    findings.extend(
        Finding(
            "BLOCK",
            TARGET,
            qa_path,
            f"qa_shape:section_missing — required section '# {name}' absent",
            fix_hint=(f"Add a `# {name}` section to QA.md per templates/feature/QA.md."),
        )
        for name in missing
    )

    if not missing and seen_order != list(_REQUIRED_SECTIONS):
        findings.append(
            Finding(
                "MEDIUM",
                TARGET,
                qa_path,
                f"qa_shape:section_out_of_order — sections appear as "
                f"{seen_order}; expected {list(_REQUIRED_SECTIONS)}",
            )
        )
    return findings, name_to_span


def _extract_section_status(section_body: str) -> str | None:
    match = _STATUS_RE.search(section_body)
    if match is None:
        return None
    return match.group("value").strip()


def _extract_section_evidence(section_body: str) -> str | None:
    match = _EVIDENCE_RE.search(section_body)
    if match is None:
        return None
    return match.group("value").strip()


def _section_status_findings(
    body: str,
    spans: dict[str, tuple[int, int]],
    qa_path: Path,
) -> tuple[list[Finding], dict[str, str]]:
    findings: list[Finding] = []
    statuses: dict[str, str] = {}
    for name in _REQUIRED_SECTIONS:
        span = spans.get(name)
        if span is None:
            continue
        section_body = body[span[0] : span[1]]
        status = _extract_section_status(section_body)
        if status is None:
            findings.append(
                Finding(
                    "BLOCK",
                    TARGET,
                    qa_path,
                    f"qa_shape:invalid_section_status — '# {name}' has no '**Status:**' line",
                    fix_hint=(f"Add a `- **Status:** <value>` line under `# {name}` in QA.md."),
                )
            )
            continue
        valid = _ACCEPTANCE_STATUSES if name == "Acceptance" else _OTHER_STATUSES
        if status not in valid:
            findings.append(
                Finding(
                    "BLOCK",
                    TARGET,
                    qa_path,
                    f"qa_shape:invalid_section_status — '# {name}' Status "
                    f"{status!r} not in {sorted(valid)}",
                    fix_hint=(f"Set `# {name}` Status to one of {sorted(valid)}."),
                )
            )
            continue
        statuses[name] = status
    return findings, statuses


def _compute_confidence(statuses: dict[str, str]) -> str:
    """Aggregate per-section Status into the canonical confidence value.

    Rules (mirrors QA.md template):

    - ``high`` ↔ Acceptance is ``delivers`` AND Edge Probing / Adversarial
      are ``pass`` AND NR Regrep is ``pass`` or ``skipped`` (a feature with
      no Negative Requirements legitimately skips that section). Any
      ``skipped`` outside NR Regrep demotes to ``partial``.
    - ``low`` ↔ any section is ``fail`` / ``does-not-deliver`` OR more
      than one section is ``partial``.
    - ``partial`` otherwise.
    """
    accept = statuses.get("Acceptance", "")
    others = {name: statuses.get(name, "") for name in _REQUIRED_SECTIONS[1:]}

    fails = sum(1 for v in (*others.values(), accept) if v in {"fail", "does-not-deliver"})
    partials = sum(1 for v in (*others.values(), accept) if v == "partial")
    if fails > 0 or partials > 1:
        return "low"

    skipped_outside_nr = any(v == "skipped" for name, v in others.items() if name != "NR Regrep")
    nr_ok = others.get("NR Regrep", "") in {"pass", "skipped"}
    non_nr_pass = all(v == "pass" for name, v in others.items() if name != "NR Regrep")
    if accept == "delivers" and non_nr_pass and nr_ok and not skipped_outside_nr:
        return "high"
    return "partial"


def _aggregation_findings(
    fm: dict[str, object],
    statuses: dict[str, str],
    qa_path: Path,
) -> list[Finding]:
    findings: list[Finding] = []
    if "Acceptance" in statuses:
        declared_verdict = fm.get("verdict")
        if statuses["Acceptance"] != declared_verdict:
            findings.append(
                Finding(
                    "BLOCK",
                    TARGET,
                    qa_path,
                    f"qa_shape:verdict_mismatch — frontmatter verdict "
                    f"{declared_verdict!r} does not match '# Acceptance' Status "
                    f"{statuses['Acceptance']!r}",
                    fix_hint=(
                        f"Set frontmatter `verdict` to {statuses['Acceptance']!r} "
                        f"or fix the `# Acceptance` Status to {declared_verdict!r}."
                    ),
                )
            )

    if all(name in statuses for name in _REQUIRED_SECTIONS):
        computed = _compute_confidence(statuses)
        declared = fm.get("confidence")
        if computed != declared:
            findings.append(
                Finding(
                    "BLOCK",
                    TARGET,
                    qa_path,
                    f"qa_shape:confidence_aggregation_mismatch — declared "
                    f"{declared!r}, computed {computed!r} from sections "
                    f"{statuses}",
                    fix_hint=(
                        f"Set frontmatter `confidence` to {computed!r} or fix the "
                        f"per-section statuses driving the aggregation."
                    ),
                )
            )
    return findings


def _evidence_findings(
    body: str,
    spans: dict[str, tuple[int, int]],
    repo_root: Path,
    qa_path: Path,
) -> list[Finding]:
    findings: list[Finding] = []
    for name in _REQUIRED_SECTIONS:
        span = spans.get(name)
        if span is None:
            continue
        section_body = body[span[0] : span[1]]
        evidence = _extract_section_evidence(section_body)
        if evidence is None or not _looks_like_path(evidence):
            continue
        candidate = (repo_root / evidence).resolve()
        if not candidate.exists():
            findings.append(
                Finding(
                    "LOW",
                    TARGET,
                    qa_path,
                    f"qa_shape:evidence_path_missing — '# {name}' Evidence "
                    f"{evidence!r} does not resolve under repo root",
                )
            )
    return findings


def _sort_findings(findings: list[Finding]) -> list[Finding]:
    def key(f: Finding) -> tuple[int, str, str]:
        code = ""
        if f.message.startswith(_CODE_PREFIX):
            rest = f.message[len(_CODE_PREFIX) :]
            code = rest.split(" ", 1)[0].rstrip(":—-")
        return (_SEVERITY_RANK.get(f.severity, 99), code, f.message)

    return sorted(findings, key=key)


def validate_qa_shape(repo_root: Path, feature_id: str) -> list[Finding]:
    """Validate ``.forge/features/<feature_id>/QA.md`` shape rules.

    Args:
        repo_root: Repository root containing the ``.forge/`` tree.
        feature_id: Slug folder name under ``.forge/features``.

    Returns:
        Sorted list of Finding records. Empty list means QA.md is
        structurally valid (or has not been authored yet and the qa
        phase has not been marked ``done``).
    """
    feature_dir = repo_root / ".forge" / "features" / feature_id
    qa_path = feature_dir / QA_FILENAME
    state_path = feature_dir / STATE_FILENAME

    if not qa_path.is_file():
        if _qa_phase_done(state_path):
            return [
                Finding(
                    "BLOCK",
                    TARGET,
                    qa_path,
                    f"qa_shape:qa_md_missing — qa phase is done but {qa_path} does not exist",
                    fix_hint=(
                        "Author QA.md from templates/feature/QA.md, or roll back the "
                        "qa phase status in state.json if QA was not actually run."
                    ),
                )
            ]
        return []

    text = _read_text(qa_path)
    if text is None:
        return [
            Finding(
                "BLOCK",
                TARGET,
                qa_path,
                f"qa_shape:qa_md_missing — {qa_path} is not a readable file",
                fix_hint=(
                    f"Restore {qa_path} as a readable UTF-8 file, or remove it and "
                    f"rerun the qa phase to regenerate QA.md."
                ),
            )
        ]

    try:
        parsed = _parse_frontmatter(text)
    except _FrontmatterParseError as exc:
        return [
            Finding(
                "BLOCK",
                TARGET,
                qa_path,
                f"qa_shape:frontmatter_missing_key — {exc}",
                fix_hint=("Fix the QA.md YAML frontmatter syntax per templates/feature/QA.md."),
            )
        ]
    if parsed is None:
        return [
            Finding(
                "BLOCK",
                TARGET,
                qa_path,
                "qa_shape:frontmatter_missing_key — QA.md has no parseable frontmatter block",
                fix_hint=("Add a `---`-delimited YAML frontmatter block at the top of QA.md."),
            )
        ]
    fm, body = parsed

    findings: list[Finding] = []
    fm_findings = _frontmatter_findings(fm, qa_path)
    findings.extend(fm_findings)

    section_findings, spans = _section_findings(body, qa_path)
    findings.extend(section_findings)

    status_findings, statuses = _section_status_findings(body, spans, qa_path)
    findings.extend(status_findings)

    # Aggregation only meaningful when frontmatter values are well-formed
    # AND the relevant section statuses parsed cleanly.
    if not fm_findings:
        findings.extend(_aggregation_findings(fm, statuses, qa_path))

    findings.extend(_evidence_findings(body, spans, repo_root, qa_path))

    return _sort_findings(findings)


__all__ = ["TARGET", "validate_qa_shape"]

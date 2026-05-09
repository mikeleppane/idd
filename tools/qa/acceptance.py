"""Acceptance black-box module for the post-ship QA phase.

Drives a fresh outsider acceptance check: takes a feature's ``SPEC.md`` plus an
opaque :class:`ArtifactDescriptor` and asks an injected runner whether each
SPEC promise is observably delivered. The module is **ecosystem-agnostic** —
it never parses ``pyproject.toml``, ``package.json``, ``Cargo.toml`` or any
other ecosystem-specific manifest. The descriptor's ``identifier`` is an
opaque string the runner interprets (a CLI invocation, an importable module
name, a URL, a container image, anything stringifiable).

Promises are extracted from three SPEC sections, in order:

- ``# Acceptance Criteria`` (or ``# Acceptance``): every numbered item or
  bullet becomes a :class:`SpecPromise` with ``source="acceptance"`` and
  ``promise_id="AC-<n>"``.
- ``# Scenarios``: every ``## Scenario:`` H2 (or ``### Scenario`` H3) header
  becomes a :class:`SpecPromise` with ``source="scenario"`` and
  ``promise_id="scenario-<n>"``. The header title is the promise text.
- ``# Intent``: the first non-blockquote paragraph is emitted as a single
  :class:`SpecPromise` with ``source="intent"`` and ``promise_id="intent-1"``.

Parsing is fence-aware via :func:`tools.validate._frontmatter._strip_code` so
illustrative fenced examples cannot smuggle promises into the live list.

Reuses :class:`QAError` from :mod:`tools.qa` so QA modules surface a single
error type.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from tools.qa import QAError
from tools.validate._frontmatter import _read_text, _strip_code

PromiseSource = Literal["acceptance", "scenario", "intent"]
PromiseStatus = Literal["met", "partial", "not_met", "skipped"]
ArtifactKind = Literal["cli", "library", "service", "ui", "other"]
AcceptanceVerdict = Literal["delivers", "partial", "does-not-deliver"]

_DEFAULT_RUNNER_OBSERVATION = "no acceptance runner configured"
_MISSING_PROMISE_OBSERVATION = "runner returned no result for this promise"

# H1 section headers for acceptance criteria. Both spellings allowed because
# real-world SPECs use either heading.
_ACCEPTANCE_BLOCK = re.compile(
    r"(?ms)^# Acceptance(?:\s+Criteria)?\b[^\n]*\n(?P<body>.*?)(?=^# |\Z)",
)

# H1 scenarios block.
_SCENARIOS_BLOCK = re.compile(
    r"(?ms)^# Scenarios\b[^\n]*\n(?P<body>.*?)(?=^# |\Z)",
)

# H1 intent block.
_INTENT_BLOCK = re.compile(
    r"(?ms)^# Intent\b[^\n]*\n(?P<body>.*?)(?=^# |\Z)",
)

# Numbered list line: `1.` `2.` etc., capturing the body text.
_NUMBERED_ITEM = re.compile(r"^\s*(?P<num>\d+)\.\s+(?P<text>.+?)\s*$")
# Bullet line.
_BULLET_ITEM = re.compile(r"^\s*[-*]\s+(?P<text>.+?)\s*$")
# Scenario header: `## Scenario:` or `### Scenario:` or `### Scenario `.
_SCENARIO_HEADER = re.compile(
    r"^\s*#{2,3}\s+Scenario(?:\s*[:\-]\s*|\s+)(?P<title>.+?)\s*$",
)


@dataclass(frozen=True)
class SpecPromise:
    """A single promise extracted from SPEC.md.

    Attributes:
        promise_id: Canonical id derived from the SPEC source — ``AC-<n>`` for
            acceptance items, ``scenario-<n>`` for scenarios, ``intent-1`` for
            the intent paragraph.
        text: User-facing promise text (criterion, scenario title, or intent
            paragraph).
        source: Which SPEC section the promise originated in.
    """

    promise_id: str
    text: str
    source: PromiseSource


@dataclass(frozen=True)
class PromiseCheck:
    """A single promise's observed status from the acceptance runner.

    Attributes:
        promise_id: Matches a :class:`SpecPromise.promise_id`.
        status: ``met`` / ``partial`` / ``not_met`` / ``skipped``.
        observation: User-facing observation phrased without internal terms
            (no file paths, function names, internal milestones).
        reproducer: Optional command, transcript line, or step list a
            reviewer can re-run to confirm the observation.
    """

    promise_id: str
    status: PromiseStatus
    observation: str
    reproducer: str | None


@dataclass(frozen=True)
class ArtifactDescriptor:
    """Opaque identifier for the shipped artifact under acceptance check.

    The module treats ``identifier`` as a black-box string; the injected
    runner is responsible for interpreting it (e.g. as a CLI command line, a
    Python importable name, a URL, a container image reference, ...).

    Attributes:
        kind: Coarse category — ``cli``, ``library``, ``service``, ``ui``,
            or ``other``. Hint to the runner; the module itself never
            branches on this value.
        identifier: Opaque artifact handle. Format is the runner's concern.
        notes: Optional human-friendly description; no semantic meaning to
            the module.
    """

    kind: ArtifactKind
    identifier: str
    notes: str = ""


@dataclass(frozen=True)
class AcceptanceResult:
    """Aggregate result of an acceptance check.

    Attributes:
        verdict: ``delivers`` when every promise is ``met``;
            ``does-not-deliver`` when any promise is ``not_met``; ``partial``
            for any other mix (e.g. mix of ``met`` / ``partial`` / ``skipped``
            with no ``not_met``).
        promises_checked: Total number of promises extracted from SPEC.md.
        promises_met: Count of promises with ``status == "met"``.
        checks: Per-promise checks in input promise order. Always one entry
            per extracted promise; missing runner results are filled with
            ``status="skipped"``.
    """

    verdict: AcceptanceVerdict
    promises_checked: int
    promises_met: int
    checks: list[PromiseCheck] = field(default_factory=list)


def _extract_acceptance_promises(stripped_text: str) -> list[SpecPromise]:
    """Pull every numbered item or bullet from the acceptance section."""
    block = _ACCEPTANCE_BLOCK.search(stripped_text)
    if block is None:
        return []
    out: list[SpecPromise] = []
    for line in block.group("body").splitlines():
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            # Defensive: nested headings inside the block — stop walking.
            break
        match = _NUMBERED_ITEM.match(line) or _BULLET_ITEM.match(line)
        if match is None:
            continue
        text = match.group("text").strip()
        if not text:
            continue
        out.append(
            SpecPromise(
                promise_id=f"AC-{len(out) + 1}",
                text=text,
                source="acceptance",
            )
        )
    return out


def _extract_scenario_promises(stripped_text: str) -> list[SpecPromise]:
    """Pull every ``## Scenario:`` or ``### Scenario:`` header title."""
    block = _SCENARIOS_BLOCK.search(stripped_text)
    if block is None:
        return []
    out: list[SpecPromise] = []
    for line in block.group("body").splitlines():
        match = _SCENARIO_HEADER.match(line)
        if match is None:
            continue
        title = match.group("title").strip()
        if not title:
            continue
        out.append(
            SpecPromise(
                promise_id=f"scenario-{len(out) + 1}",
                text=title,
                source="scenario",
            )
        )
    return out


def _extract_intent_promise(stripped_text: str) -> list[SpecPromise]:
    """Return the first non-blockquote paragraph of ``# Intent`` as a promise."""
    block = _INTENT_BLOCK.search(stripped_text)
    if block is None:
        return []
    paragraph_lines: list[str] = []
    started = False
    for line in block.group("body").splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            # Skip template blockquotes — they are placeholder hints.
            if started:
                break
            continue
        if not stripped:
            if started:
                break
            continue
        if stripped.startswith("#"):
            break
        started = True
        paragraph_lines.append(stripped)
    if not paragraph_lines:
        return []
    text = " ".join(paragraph_lines).strip()
    if not text:
        return []
    return [SpecPromise(promise_id="intent-1", text=text, source="intent")]


def parse_spec_promises(spec_md_text: str) -> list[SpecPromise]:
    """Extract promises from SPEC.md text.

    Order: acceptance criteria → scenarios → intent. Fence-aware via
    :func:`_strip_code`.

    Args:
        spec_md_text: Raw SPEC.md contents.

    Returns:
        Ordered list of :class:`SpecPromise`. Empty when the SPEC has none
        of the three recognised sections.
    """
    stripped = _strip_code(spec_md_text)
    promises: list[SpecPromise] = []
    promises.extend(_extract_acceptance_promises(stripped))
    promises.extend(_extract_scenario_promises(stripped))
    promises.extend(_extract_intent_promise(stripped))
    return promises


def _default_runner(
    artifact: ArtifactDescriptor,
    promises: list[SpecPromise],
) -> list[PromiseCheck]:
    """Return ``skipped`` for every promise — no runner wired in."""
    del artifact  # opaque to the default runner
    return [
        PromiseCheck(
            promise_id=p.promise_id,
            status="skipped",
            observation=_DEFAULT_RUNNER_OBSERVATION,
            reproducer=None,
        )
        for p in promises
    ]


def _aggregate_verdict(checks: list[PromiseCheck]) -> AcceptanceVerdict:
    """Fold per-check statuses into the acceptance-level verdict."""
    if not checks:
        return "partial"
    if any(c.status == "not_met" for c in checks):
        return "does-not-deliver"
    if all(c.status == "met" for c in checks):
        return "delivers"
    return "partial"


def run_acceptance(
    repo_root: Path,
    feature_id: str,
    artifact: ArtifactDescriptor,
    *,
    runner: Callable[[ArtifactDescriptor, list[SpecPromise]], list[PromiseCheck]] | None = None,
) -> AcceptanceResult:
    """Run the black-box acceptance check for a feature.

    Args:
        repo_root: Repository root the feature folder resolves under.
        feature_id: Feature identifier (e.g. ``2026-05-09-example``).
        artifact: Opaque descriptor of the shipped artifact. The instance is
            forwarded to the runner unchanged.
        runner: Optional callable taking ``(artifact, promises)`` and
            returning a list of :class:`PromiseCheck`. When omitted, every
            promise is marked ``skipped`` and the verdict is ``partial`` so
            the QA record never claims confidence the run did not earn.

    Returns:
        :class:`AcceptanceResult` with ``checks`` in input promise order.
        Promises the runner did not return are auto-filled with
        ``status="skipped"`` and an explanatory observation.

    Raises:
        QAError: When the feature's SPEC.md is missing.
    """
    spec_path = repo_root / ".forge" / "features" / feature_id / "SPEC.md"
    spec_text = _read_text(spec_path)
    if spec_text is None:
        raise QAError(f"SPEC.md missing for feature {feature_id!r} at {spec_path}")

    promises = parse_spec_promises(spec_text)
    active_runner = runner if runner is not None else _default_runner
    raw_checks = active_runner(artifact, promises)

    by_id: dict[str, PromiseCheck] = {}
    valid_ids = {p.promise_id for p in promises}
    for check in raw_checks:
        # Drop any results whose promise_id does not match an extracted
        # promise — those are runner errors, not acceptance findings.
        if check.promise_id in valid_ids:
            by_id[check.promise_id] = check

    ordered_checks: list[PromiseCheck] = []
    for promise in promises:
        existing = by_id.get(promise.promise_id)
        if existing is not None:
            ordered_checks.append(existing)
        else:
            ordered_checks.append(
                PromiseCheck(
                    promise_id=promise.promise_id,
                    status="skipped",
                    observation=_MISSING_PROMISE_OBSERVATION,
                    reproducer=None,
                )
            )

    promises_met = sum(1 for c in ordered_checks if c.status == "met")
    verdict = _aggregate_verdict(ordered_checks)

    return AcceptanceResult(
        verdict=verdict,
        promises_checked=len(promises),
        promises_met=promises_met,
        checks=ordered_checks,
    )

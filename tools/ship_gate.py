"""Constitution ship-time gate (M3 spec §5.3.9 / D-4a).

Four pure functions:

    parse_review_findings(path)                  -> list[ShipFinding]
        Filters rows to Status: open. Resolved/accepted-risk are history.
    partition_by_article_level(findings, articles)
                                                 -> (gate, warn, info)
    render_gate_prompt(gate, articles)           -> str
    render_warn_summary(warn, articles)          -> str
    make_acknowledgement_hook(...)               -> Callable[[Path], None]
        Returns a closure suitable for ship_feature(pre_archive_hook=...).
        Hook records the ACK INSIDE the transactional ship; preflight
        failures raise ArchiveError before the hook ever runs (no ghost
        deviation for an aborted ship).

The skill orchestrator (idd-ship) decides what to do with each partition:
    gate  -> render_gate_prompt(...) + prompt user; on ACKNOWLEDGE compose
             ack_hook with _mark_done and pass to ship_feature. On 'a' or
             'b' the orchestrator halts and surfaces remediation.
    warn  -> render in summary; no gate, no acknowledge.
    info  -> log only.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tools.constitution import Article


class ShipGateError(RuntimeError):
    """Raised when the gate cannot record the acknowledgement state."""


_TAG_RE = re.compile(r"\[constitution:(A\d+)\]")
# Header row of the Findings table tells us which column holds Status.
_HEADER_RE = re.compile(r"^\|\s*ID\s*\|", re.IGNORECASE)
_VALID_STATUS_VALUES: frozenset[str] = frozenset({"open", "resolved", "accepted-risk"})


@dataclass(frozen=True, kw_only=True)
class ShipFinding:
    """One unresolved REVIEW.code.md finding tagged ``[constitution:A<n>]``."""

    article_id: str | None
    severity: str  # BLOCK|HIGH|MEDIUM|LOW
    location: str
    message: str


def _parse_table_columns(line: str) -> list[str]:
    """Split a markdown table row into trimmed cell values."""
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def parse_review_findings(path: Path) -> list[ShipFinding]:
    """Parse REVIEW.code.md for ``Status: open`` findings tagged ``[constitution:A<n>]``.

    Resolved or accepted-risk rows are convergence-history (Open Scoping #15)
    and skipped — the §5.3.9 gate acts on unresolved findings only.

    Missing file returns []. Robust to extra whitespace; tolerates the legacy
    no-Status layout for backwards compat (treats every row as ``open``) so a
    REVIEW.code.md authored before the column was added still surfaces
    findings. An unrecognized Status cell value (anything outside
    ``{open, resolved, accepted-risk}``, case-insensitive) raises
    ``ShipGateError`` so a typo cannot silently filter the row.

    Args:
        path: Path to REVIEW.code.md.

    Returns:
        List of unresolved ``[constitution:A<n>]``-tagged findings.

    Raises:
        ShipGateError: When a row's Status cell holds an unrecognized value.
    """
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    header_idx = next(
        (i for i, line in enumerate(lines) if _HEADER_RE.match(line)),
        None,
    )
    if header_idx is None:
        return []
    header = _parse_table_columns(lines[header_idx])
    try:
        status_col = header.index("Status")
    except ValueError:
        status_col = -1  # legacy layout; treat all rows as open

    out: list[ShipFinding] = []
    for line in lines[header_idx + 1 :]:
        if not line.startswith("| F-"):
            continue
        cells = _parse_table_columns(line)
        if len(cells) < len(header):
            continue
        # Locate columns by header index for resilience.
        try:
            severity = cells[header.index("Severity")]
            location = cells[header.index("Location")]
            message = cells[header.index("Problem")]
        except ValueError:
            continue  # malformed table; skip
        if status_col >= 0:
            row_status = cells[status_col].lower()
            if row_status not in _VALID_STATUS_VALUES:
                raise ShipGateError(f"unrecognized Status value: {cells[status_col]!r} in {path}")
            if row_status != "open":
                continue
        tag_match = _TAG_RE.search(message)
        if tag_match is None:
            continue
        out.append(
            ShipFinding(
                article_id=tag_match.group(1),
                severity=severity,
                location=location,
                message=message,
            )
        )
    return out


def partition_by_article_level(
    findings: Iterable[ShipFinding],
    articles: list[Article],
) -> tuple[list[ShipFinding], list[ShipFinding], list[ShipFinding]]:
    """Bucket findings into (gate, warn, info).

    Args:
        findings: Iterable of parsed ``ShipFinding`` rows.
        articles: Loaded Constitution articles used to resolve levels.

    Returns:
        Tuple ``(gate, warn, info)``:
            - ``gate``: severity in ``{BLOCK, HIGH, MEDIUM}`` AND article level
              == ``CRITICAL``.
            - ``warn``: severity in ``{BLOCK, HIGH, MEDIUM}`` AND article level
              == ``SHOULD``.
            - ``info``: everything else with a known article id, plus findings
              whose article id is not in ``articles``.
    """
    by_id = {a.id: a for a in articles}
    gate: list[ShipFinding] = []
    warn: list[ShipFinding] = []
    info: list[ShipFinding] = []
    for f in findings:
        article = by_id.get(f.article_id) if f.article_id else None
        if article is None:
            info.append(f)
            continue
        meets_severity = f.severity in {"BLOCK", "HIGH", "MEDIUM"}
        if not meets_severity:
            info.append(f)
            continue
        if article.level == "CRITICAL":
            gate.append(f)
        elif article.level == "SHOULD":
            warn.append(f)
        else:
            info.append(f)
    return gate, warn, info


def render_gate_prompt(
    gate: list[ShipFinding],
    articles: list[Article],
) -> str:
    """Render the ship-time gate prompt for CRITICAL findings.

    Args:
        gate: Findings bucketed into the gate partition.
        articles: Loaded Constitution articles.

    Returns:
        Multiline string suitable for printing to the user. Empty string when
        ``gate`` is empty.
    """
    if not gate:
        return ""
    by_id = {a.id: a for a in articles}
    lines = [
        "=" * 57,
        "  CONSTITUTION FINDINGS - UNRESOLVED AT SHIP",
        "=" * 57,
        "",
        f"The reviewer flagged {len(gate)} finding(s) against project Constitution",
        "articles. M3 does not BLOCK on these - you are the gate.",
        "",
    ]
    for f in gate:
        article = by_id.get(f.article_id or "")
        title = article.title if article else "(unknown)"
        rationale = article.reference or article.rationale or "—" if article else "—"
        lines.append(f'[constitution:{f.article_id}] {f.severity} (CRITICAL article: "{title}")')
        lines.append(f"  File: {f.location}")
        lines.append(f"  Reviewer note: {f.message}")
        lines.append(f"  Article rationale: {rationale}")
        lines.append("")
    lines.extend(
        [
            "To proceed, you must do ONE of:",
            "  (a) Resolve the finding (edit code, re-run /idd:review --target code, /idd:verify, /idd:ship).",
            "  (b) Log a Constitution exception in decisions.md (template printed below) and re-run.",
            "  (c) Type 'ACKNOWLEDGE' to ship anyway. The acknowledgement is recorded in",
            "      state.json.deviations[] and decisions.md, both persisting into the archive.",
            "",
            "Choice [a/b/c]:",
        ]
    )
    return "\n".join(lines)


def render_warn_summary(
    warn: list[ShipFinding],
    articles: list[Article],
) -> str:
    """Render the SHOULD-level advisory summary for the ship report.

    Args:
        warn: Findings bucketed into the warn partition.
        articles: Loaded Constitution articles.

    Returns:
        Multiline summary string. Empty string when ``warn`` is empty.
    """
    if not warn:
        return ""
    by_id = {a.id: a for a in articles}
    lines = ["Constitution SHOULD findings (advisory):"]
    for f in warn:
        article = by_id.get(f.article_id or "")
        title = article.title if article else "(unknown)"
        lines.append(
            f"  - [constitution:{f.article_id}] {f.severity} {f.location} — {title}: {f.message}"
        )
    return "\n".join(lines)


# Decisions heading title and deviation cause MUST share their first 60
# characters (case-insensitive) so `tools.validate.validate_deviations`
# cross-ref passes. Keep these literals adjacent so future edits stay aligned.
_DECISIONS_HEADING_PREFIX = "Constitution finding acknowledged at ship"
_DEVIATION_CAUSE_PREFIX = "Constitution finding acknowledged at ship"


def make_acknowledgement_hook(
    *,
    state_path: Path,
    decisions_path: Path,
    gate_findings: list[ShipFinding],
    articles: list[Article],
    now: datetime | None = None,
) -> Callable[[Path], None]:
    """Return a closure that records the ACK on the live feature folder.

    The closure matches ``ship_feature(pre_archive_hook=...)`` signature and is
    invoked AFTER ship_feature's preflight passes but BEFORE the archive move.
    If preflight fails, the hook never runs — no ghost deviation (Open
    Scoping #14). Hook failure rolls back ship_feature's canonical write.

    Combine with ``_mark_done`` in idd-ship via::

        def composed(source: Path) -> None:
            ack_hook(source)
            mark_done(source)

        ship_feature(..., pre_archive_hook=composed)

    Args:
        state_path: Path to the live feature ``state.json``.
        decisions_path: Path to the live feature ``decisions.md``.
        gate_findings: Findings the user explicitly ACKNOWLEDGED.
        articles: Loaded Constitution articles (for title lookup).
        now: Optional fixed timestamp (defaults to ``datetime.now(UTC)``).

    Returns:
        Callable matching ``Callable[[Path], None]`` for ``ship_feature``.

    Raises:
        ShipGateError: When ``state_path`` does not exist at hook-build time.
    """
    if not state_path.exists():
        raise ShipGateError(f"state.json not found at {state_path}")
    now = now or datetime.now(UTC)
    iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    by_id = {a.id: a for a in articles}

    cause = (
        _DEVIATION_CAUSE_PREFIX
        + ": "
        + ", ".join(f"[constitution:{f.article_id}]" for f in gate_findings)
    )

    def _record(_source: Path) -> None:
        # Two-sided idempotency: this hook may re-run after a partial-write
        # failure (e.g. decisions.md succeeded but state.json write raised, the
        # outer `ship_feature` rolled back the canonical-spec write, and the
        # caller is now retrying). Treat the ACK as already-applied if EITHER
        # sink already records it. A bare decisions.md heading without the
        # matching state.json deviation entry is the recovery scenario we must
        # tolerate so the second attempt can complete the state.json write
        # without appending a duplicate decisions heading.
        payload = json.loads(state_path.read_text(encoding="utf-8"))
        deviations: list[dict[str, str]] = payload.setdefault("deviations", [])
        already_in_state = any(d.get("cause") == cause for d in deviations)
        if already_in_state:
            return

        decisions_text = (
            decisions_path.read_text(encoding="utf-8") if decisions_path.exists() else ""
        )
        # Match the heading by its (cause-bearing) "Cause:" body line so the
        # idempotency check survives heading-date drift across retries.
        already_in_decisions = f"Cause: {cause}" in decisions_text

        # Step 1: append the decisions.md heading FIRST. An orphan heading
        # without a matching state.json deviation is silent under
        # `validate_deviations` (the validator keys on deviations[]), whereas
        # the reverse — state.json deviation without the decisions heading —
        # is a non-recoverable BLOCK on the next /idd:validate run.
        if not already_in_decisions:
            body_lines = [
                "",
                f"## {now.date().isoformat()} — {_DECISIONS_HEADING_PREFIX}",
                "",
                # Echo the deviation cause verbatim so `validate_deviations`'
                # 60-char substring cross-ref locates it inside the body block
                # regardless of how many tags accumulate on the cause line.
                f"Cause: {cause}",
            ]
            for f in gate_findings:
                article = by_id.get(f.article_id or "")
                title = article.title if article else "(unknown)"
                body_lines.append(
                    f"- [constitution:{f.article_id}] **{title}** — {f.location} — {f.message}"
                )
            with decisions_path.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(body_lines) + "\n")

        # Step 2: mutate state.json. If this raises (ENOSPC, permission, …)
        # the caller's outer transaction rolls back and a retry will find the
        # decisions heading already present (skipped above) and only complete
        # the state.json write — exactly one deviation, exactly one heading.
        deviations.append(
            {
                "phase": "ship",
                "cause": cause,
                "resolution": "user_acknowledged",
                "logged_at": iso,
            }
        )
        state_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    return _record

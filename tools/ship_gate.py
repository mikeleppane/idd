"""Constitution ship-time gate (M3 spec §5.3.9 / D-4a).

Four pure functions:

    parse_review_findings(path)                  -> list[ShipFinding]
        Filters rows to Status: open. Resolved/accepted-risk are history.

        Multi-tag rows (a single `Problem` cell containing more than one
        ``[constitution:A<n>]`` tag) emit ONE ``ShipFinding`` per tag so a
        SHOULD-then-CRITICAL ordering cannot silently demote the CRITICAL
        finding into the warn bucket. Each emitted finding routes through
        ``partition_by_article_level`` independently.

        Header-row anchoring: the parser locates the `# Findings` heading
        (case-insensitive) and binds to the first `| ID |` table that
        follows. A `| ID |` table appearing in the document preamble (e.g.
        an inventory or table of contents) is therefore not mistaken for
        the Findings table. With no `# Findings` heading present the
        parser returns ``[]``.

    partition_by_article_level(findings, articles)
                                                 -> (gate, warn, info)
    render_gate_prompt(gate, articles)           -> str
    render_warn_summary(warn, articles)          -> str
    make_acknowledgement_hook(...)               -> Callable[[Path], None]
        Returns a closure suitable for ship_feature(pre_archive_hook=...).
        Hook records the ACK INSIDE the transactional ship; preflight
        failures raise ArchiveError before the hook ever runs (no ghost
        deviation for an aborted ship).

The skill orchestrator (forge-ship) decides what to do with each partition:
    gate  -> render_gate_prompt(...) + prompt user; on ACKNOWLEDGE compose
             ack_hook with _mark_done and pass to ship_feature. On 'a' or
             'b' the orchestrator halts and surfaces remediation.
    warn  -> render in summary; no gate, no acknowledge.
    info  -> log only.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tools.constitution import Article
from tools.constitution_amend import atomic_replace, ensure_decisions_file
from tools.validate import Finding
from tools.validate.git_conventions import validate_git_conventions


class ShipGateError(RuntimeError):
    """Raised when the gate cannot record the acknowledgement state."""


_TAG_RE = re.compile(r"\[constitution:(A\d+)\]")
# Header row of the Findings table tells us which column holds Status.
_HEADER_RE = re.compile(r"^\|\s*ID\s*\|", re.IGNORECASE)
# Anchor the table search to the `# Findings` heading (case-insensitive) so
# unrelated `| ID | ... |` tables in the preamble cannot disarm the parser.
_FINDINGS_HEADING_RE = re.compile(r"^#\s+Findings\s*$", re.IGNORECASE)
_VALID_STATUS_VALUES: frozenset[str] = frozenset({"open", "resolved", "accepted-risk"})
# Closed 4-value enum from the REVIEW.md template. Keeping severity as a
# closed vocabulary makes routing decisions deterministic and makes typos
# loud — a `severity='Lo'` on a CRITICAL-tagged row otherwise silently bypassed
# the gate via the old `severity in {BLOCK,HIGH,MEDIUM}` short-circuit.
_VALID_SEVERITY_VALUES: frozenset[str] = frozenset({"BLOCK", "HIGH", "MEDIUM", "LOW"})


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

    Multi-tag rows: a `Problem` cell may carry more than one
    ``[constitution:A<n>]`` tag (e.g. one finding violates two articles, or a
    SHOULD article is mentioned alongside a CRITICAL article). The parser
    emits one ``ShipFinding`` per tag so each tag routes through
    ``partition_by_article_level`` on its own merits — no silent demotion of
    a CRITICAL article behind a SHOULD article that happened to appear first.

    Header-row anchoring: the parser locates the ``# Findings`` heading
    (case-insensitive) and binds to the first ``| ID |`` table that follows
    it. Any ``| ID |`` table appearing in the document preamble (inventory,
    table of contents, etc.) is ignored — pre-fix the parser greedily latched
    onto the first preamble table and silently zeroed every downstream row.

    Missing file returns ``[]``. No ``# Findings`` heading returns ``[]``.
    Robust to extra whitespace; tolerates the legacy no-Status layout for
    backwards compat (treats every row as ``open``) so a REVIEW.code.md
    authored before the column was added still surfaces findings. An
    unrecognized Status cell value (anything outside
    ``{open, resolved, accepted-risk}``, case-insensitive) raises
    ``ShipGateError`` so a typo cannot silently filter the row. Severity
    cells must come from the closed ``{BLOCK, HIGH, MEDIUM, LOW}`` vocabulary
    (REVIEW.md template); typos and case mismatches raise ``ShipGateError``
    instead of silently bypassing the gate.

    Asymmetry vs ``validate_deviations``: short or malformed ``| F-`` rows
    are silently skipped (``continue``) here, whereas the deviation
    validator BLOCKs on the same shape. The skip is intentional — a
    REVIEW.code.md is mid-edit during the review convergence loop and
    blowing up on a half-typed row would force the user to finish the row
    before re-running the gate. The deviation file is point-in-time audit
    trail and earns the stricter check.

    Args:
        path: Path to REVIEW.code.md.

    Returns:
        List of unresolved ``[constitution:A<n>]``-tagged findings, one
        entry per tag in each matching row.

    Raises:
        ShipGateError: When a row's Status or Severity cell holds an
            unrecognized value.
    """
    if not path.exists():
        return []

    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Locate the `# Findings` heading first; the table parser only considers
    # rows AFTER this anchor so a `| ID | ... |` table in the preamble cannot
    # disarm the parser. Without the heading, return [] to match the
    # documented "no findings" contract.
    findings_heading_idx = next(
        (i for i, line in enumerate(lines) if _FINDINGS_HEADING_RE.match(line)),
        None,
    )
    if findings_heading_idx is None:
        return []
    search_start = findings_heading_idx + 1

    header_idx = next(
        (
            i
            for i, line in enumerate(lines[search_start:], start=search_start)
            if _HEADER_RE.match(line)
        ),
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
        out.extend(_findings_from_row(line, header=header, status_col=status_col, source=path))
    return out


def _findings_from_row(
    line: str,
    *,
    header: list[str],
    status_col: int,
    source: Path,
) -> list[ShipFinding]:
    """Yield zero or more ShipFinding rows from a single `| F-...` table line.

    A row produces multiple findings when its `Problem` cell carries more than
    one ``[constitution:A<n>]`` tag (multi-violation finding). Splitting this
    out of ``parse_review_findings`` keeps the parent's branch count under the
    PLR0912 ceiling and isolates per-row routing logic.
    """
    cells = _parse_table_columns(line)
    if len(cells) < len(header):
        return []
    try:
        severity = cells[header.index("Severity")]
        location = cells[header.index("Location")]
        message = cells[header.index("Problem")]
    except ValueError:
        return []  # malformed table; skip
    # Tag check FIRST. The closed Status / Severity vocabularies only matter
    # for constitution-tagged rows (those are the ones that influence the
    # gate). An untagged row with an unusual Status (e.g. "in-progress" or a
    # typo) is reviewer convergence-history that this parser can ignore;
    # validating its Status would raise ShipGateError on rows the gate
    # never cared about in the first place.
    tag_ids = _TAG_RE.findall(message)
    if not tag_ids:
        return []
    if status_col >= 0:
        row_status = cells[status_col].lower()
        if row_status not in _VALID_STATUS_VALUES:
            raise ShipGateError(f"unrecognized Status value: {cells[status_col]!r} in {source}")
        if row_status != "open":
            return []
    # Severity vocabulary is a closed 4-value enum from the REVIEW.md template.
    # Validating here (instead of in the partitioner) makes typos loud and
    # lets `partition_by_article_level` route purely on article level.
    if severity not in _VALID_SEVERITY_VALUES:
        raise ShipGateError(f"unrecognized Severity value: {severity!r} in {source}")
    # findall keeps every tag in declaration order; one ShipFinding per tag
    # so each routes through partition_by_article_level on its own merits.
    return [
        ShipFinding(
            article_id=article_id,
            severity=severity,
            location=location,
            message=message,
        )
        for article_id in tag_ids
    ]


def partition_by_article_level(
    findings: Iterable[ShipFinding],
    articles: list[Article],
) -> tuple[list[ShipFinding], list[ShipFinding], list[ShipFinding]]:
    """Bucket findings into (gate, warn, info) by ARTICLE LEVEL alone.

    Severity is treated as advisory metadata at this layer — the SKILL
    contract is "CRITICAL article -> gate, SHOULD article -> warn, MAY
    article -> info" regardless of the reviewer-assigned severity. Routing
    purely on article level closes a hole where a `severity='LOW'` cell on a
    CRITICAL article silently bypassed the gate via an old short-circuit.
    The closed severity vocabulary itself is enforced upstream in
    ``parse_review_findings`` so unrecognized values cannot reach the
    partitioner.

    Args:
        findings: Iterable of parsed ``ShipFinding`` rows.
        articles: Loaded Constitution articles used to resolve levels.

    Returns:
        Tuple ``(gate, warn, info)``:
            - ``gate``: article level == ``CRITICAL``.
            - ``warn``: article level == ``SHOULD``.
            - ``info``: article level == ``MAY``, plus findings whose
              article id is not present in ``articles`` (unknown article).
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

    Raises:
        ShipGateError: When any gate-bucket finding references an article id
            that is not present in ``articles``. Defense in depth: the
            partitioner already routes unknown ids to info, so this branch
            is unreachable in production. The assertion documents the
            invariant so a future caller bypassing the partitioner cannot
            smuggle a "(unknown)" rendering past the user prompt.
    """
    if not gate:
        return ""
    by_id = {a.id: a for a in articles}
    unknown = sorted({f.article_id for f in gate if f.article_id and f.article_id not in by_id})
    if unknown:
        raise ShipGateError(f"render_gate_prompt: unknown article id(s) in gate bucket: {unknown}")
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
        # Prefer the article's reference (e.g. OWASP entry) over its rationale
        # because reference is the canonical citation; rationale is the back-
        # ground. The variable name reflects what we actually display.
        context = (article.reference or article.rationale or "—") if article else "—"
        lines.append(f'[constitution:{f.article_id}] {f.severity} (CRITICAL article: "{title}")')
        lines.append(f"  File: {f.location}")
        lines.append(f"  Reviewer note: {f.message}")
        lines.append(f"  Article context: {context}")
        lines.append("")
    lines.extend(
        [
            "To proceed, you must do ONE of:",
            "  (a) Resolve the finding (edit code, re-run /forge:review --target code, /forge:verify, /forge:ship).",
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
# cross-ref passes. Single literal — the heading and the cause string are
# textually identical, so a single source kills the drift risk that two
# parallel constants invited.
_ACK_PREFIX = "Constitution finding acknowledged at ship"


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

    Concurrency note: state.json mutation goes through an atomic
    tmpfile+rename pair (``tools.constitution_amend.atomic_replace``) so a
    crash mid-write leaves the canonical file pointed at the previous valid
    payload, not a partial mix. The two-sided idempotency check at the top
    of the closure makes safe retries deterministic — a second call after a
    crashed state.json write detects nothing in deviations[], finds the
    orphan decisions.md heading, and completes only the state.json write.

    Decisions.md bootstrap: if ``decisions_path`` is absent the closure
    creates it with the standard ``# Decisions`` H1 via the shared
    ``ensure_decisions_file`` helper before appending the ACK heading. This
    matches what the amend lifecycle produces so a fresh feature folder
    never ends up with a header-less decisions.md that downstream
    validators reject.

    Combine with ``_mark_done`` in forge-ship via::

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
        ShipGateError: When ``state_path`` does not exist at hook-build time,
            or when the file exists but is not parseable JSON at hook-call
            time. Wrapping ``JSONDecodeError`` here keeps the failure mode
            on-domain — ship_feature's outer ArchiveError wrap surfaces a
            clear "state.json corrupt" cause instead of a raw decoder
            traceback.
    """
    if not state_path.exists():
        raise ShipGateError(f"state.json not found at {state_path}")
    now = now or datetime.now(UTC)
    iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    by_id = {a.id: a for a in articles}

    cause = _ACK_PREFIX + ": " + ", ".join(f"[constitution:{f.article_id}]" for f in gate_findings)

    def _record(_source: Path) -> None:
        # Two-sided idempotency: this hook may re-run after a partial-write
        # failure (e.g. decisions.md succeeded but state.json write raised, the
        # outer `ship_feature` rolled back the canonical-spec write, and the
        # caller is now retrying). Treat the ACK as already-applied if EITHER
        # sink already records it. A bare decisions.md heading without the
        # matching state.json deviation entry is the recovery scenario we must
        # tolerate so the second attempt can complete the state.json write
        # without appending a duplicate decisions heading.
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            # On-domain wrap so ship_feature's outer ArchiveError surfaces a
            # readable "state.json is corrupt" message instead of a raw
            # decoder traceback. Re-running the hook against a corrupt file
            # would never recover; the caller must repair state.json first.
            raise ShipGateError(f"state.json is corrupt: {exc}") from exc
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
        # is a non-recoverable BLOCK on the next /forge:validate run.
        if not already_in_decisions:
            # Bootstrap decisions.md with `# Decisions` H1 if absent so a
            # fresh feature folder ends up with the same shape the amend
            # lifecycle produces. Shared helper keeps both paths in sync.
            ensure_decisions_file(decisions_path)
            body_lines = [
                "",
                f"## {now.date().isoformat()} — {_ACK_PREFIX}",
                "",
                # Echo the deviation cause verbatim so `validate_deviations`'
                # 60-char substring cross-ref locates it inside the body block
                # regardless of how many tags accumulate on the cause line.
                f"Cause: {cause}",
            ]
            for f in gate_findings:
                article = by_id.get(f.article_id or "")
                title = article.title if article else "(unknown)"
                # Strip a leading [constitution:A<n>] from the reviewer
                # message so the tag is not echoed twice on the same line —
                # the bullet already starts with the tag.
                clean_message = _TAG_RE.sub("", f.message, count=1).lstrip(" -")
                body_lines.append(
                    f"- [constitution:{f.article_id}] **{title}** — {f.location} — {clean_message}"
                )
            with decisions_path.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(body_lines) + "\n")

        # Step 2: mutate state.json via atomic-replace (tmpfile + rename).
        # Direct `state_path.write_text(...)` could leave a half-written
        # state.json on a crash mid-write; the rename is the single moment
        # the canonical name flips. On a retry after partial failure, the
        # idempotency check above (already_in_state) short-circuits before
        # reaching this line.
        deviations.append(
            {
                "phase": "ship",
                "cause": cause,
                "resolution": "user_acknowledged",
                "logged_at": iso,
            }
        )
        atomic_replace(state_path, json.dumps(payload, indent=2) + "\n")

    return _record


# --- git-conventions wiring (WS2) -----------------------------------------
#
# The forge-ship orchestrator routes git-convention findings through these
# helpers so it never has to know the validator's import path or the bucket
# vocabulary. Three layers: severity-only partition, runner-injected
# evaluator, and two pure renderers for the gate prompt + non-blocking
# warn summary.

_GIT_CONV_GATE_HEADER = "BLOCK / HIGH git-convention violations detected:"
_GIT_CONV_GATE_FOOTER = (
    "Resolve by amending the commits, force-pushing a clean history (if\n"
    "ship policy allows), or recording an explicit decisions.md ADR\n"
    "before re-running /forge:ship."
)
_GIT_CONV_WARN_HEADER = "Git-convention findings (advisory):"


@dataclass(frozen=True, kw_only=True)
class GitConventionGatePartition:
    """Three-way split of git_conventions Findings for the ship gate.

    Attributes:
        gate: Severity in ``{BLOCK, HIGH}``; blocks ship until resolved or
            acknowledged via decisions.md.
        warn: Severity ``MEDIUM``; surface but allow ship.
        info: Severity in ``{LOW, WARN, INFO}`` plus any out-of-vocabulary
            value (defensive — should not occur for typed callers, but
            protects against deserialized JSON inputs).
    """

    gate: tuple[Finding, ...]
    warn: tuple[Finding, ...]
    info: tuple[Finding, ...]


def partition_git_conventions(findings: Iterable[Finding]) -> GitConventionGatePartition:
    """Bucket git_conventions Findings by severity for ship-gate routing.

    BLOCK / HIGH route to ``gate`` (ship is blocked until resolved or
    acknowledged), MEDIUM routes to ``warn`` (surface but allow ship), and
    LOW / WARN route to ``info`` (log only). Any severity outside the closed
    vocabulary also routes to ``info`` so a deserialized JSON payload with
    an unfamiliar severity cannot crash the gate — mypy would normally
    reject this branch but the runtime check protects untyped call sites.

    Order preservation: within each bucket, findings keep their original
    declaration order. ``validate_git_conventions`` already sorts by
    ``(commit_index, severity_rank, message)``, so preserving input order
    here keeps test assertions stable.

    Args:
        findings: Any iterable of ``Finding`` instances. Generators are
            accepted; the partition tuple-ifies before returning so the
            result stays immutable.

    Returns:
        :class:`GitConventionGatePartition` with frozen tuple fields.
    """
    gate: list[Finding] = []
    warn: list[Finding] = []
    info: list[Finding] = []
    for f in findings:
        severity = f.severity
        if severity in ("BLOCK", "HIGH"):
            gate.append(f)
        elif severity == "MEDIUM":
            warn.append(f)
        else:
            # LOW, WARN, INFO, or any out-of-vocabulary string — all advisory
            # for the ship gate. Defense in depth against deserialized inputs.
            info.append(f)
    return GitConventionGatePartition(
        gate=tuple(gate),
        warn=tuple(warn),
        info=tuple(info),
    )


def evaluate_git_conventions_gate(
    feature_folder: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> GitConventionGatePartition:
    """Run :func:`validate_git_conventions` and partition by severity.

    Pure dispatch wrapper so the forge-ship skill does not need to know the
    validator's import path. The runner seam is forwarded to the underlying
    validator unchanged.

    Args:
        feature_folder: Path to ``.forge/features/<feature-id>/``.
        runner: Subprocess seam, forwarded to
            :func:`validate_git_conventions`. ``None`` selects the production
            runner with the default 10 s timeout.

    Returns:
        :class:`GitConventionGatePartition`; empty buckets when there are no
        findings.
    """
    findings = validate_git_conventions(feature_folder, runner=runner)
    return partition_git_conventions(findings)


def render_git_conventions_gate_prompt(partition: GitConventionGatePartition) -> str:
    """Render the gate bucket as a human-readable prompt for forge-ship.

    Empty gate bucket returns the empty string (the caller does not show the
    prompt). Otherwise one line per finding of the form
    ``- <message>  [<severity>]`` (no quoting around the message), plus a
    single footer describing the recovery actions.

    Args:
        partition: A partition produced by :func:`partition_git_conventions`
            (directly or via :func:`evaluate_git_conventions_gate`).

    Returns:
        Multiline string suitable for printing to the user, or ``""`` when
        the gate bucket is empty.
    """
    if not partition.gate:
        return ""
    lines = [_GIT_CONV_GATE_HEADER]
    lines.extend(f"- {f.message}  [{f.severity}]" for f in partition.gate)
    lines.append("")
    lines.append(_GIT_CONV_GATE_FOOTER)
    return "\n".join(lines)


def render_git_conventions_warn_summary(partition: GitConventionGatePartition) -> str:
    """Render MEDIUM (warn) findings as a non-blocking summary line block.

    Empty warn bucket returns the empty string. One line per finding,
    structure mirrors :func:`render_git_conventions_gate_prompt` so the
    surrounding skill prose can render either with a single visual rhythm.

    Args:
        partition: A partition produced by :func:`partition_git_conventions`.

    Returns:
        Multiline string, or ``""`` when the warn bucket is empty.
    """
    if not partition.warn:
        return ""
    lines = [_GIT_CONV_WARN_HEADER]
    lines.extend(f"- {f.message}  [{f.severity}]" for f in partition.warn)
    return "\n".join(lines)

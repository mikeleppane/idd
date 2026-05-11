"""Constitution + trap-memory ship-time gate.

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
from typing import TYPE_CHECKING, Literal

from tools.constitution import Article
from tools.constitution_amend import atomic_replace, ensure_decisions_file
from tools.validate import Finding
from tools.validate.git_conventions import validate_git_conventions

if TYPE_CHECKING:
    from tools.intel.lessons import Lesson


class ShipGateError(RuntimeError):
    """Raised when the gate cannot record the acknowledgement state."""


_TAG_RE = re.compile(r"\[constitution:(A\d+)\]")
_LESSON_TAG_RE = re.compile(r"\[lesson:(L\d+)\]")
# Lesson severity vocabulary {CRITICAL, HIGH, MEDIUM, LOW} mapped onto the
# ship-gate severity vocabulary {BLOCK, HIGH, MEDIUM, LOW}. The reviewer copies
# the lesson's Severity field into the row's Severity cell after running it
# through this map, so the ship-gate parser can keep treating the Severity cell
# as the closed _VALID_SEVERITY_VALUES vocabulary. The partitioner cross-checks
# the cell against the lesson's source-of-truth Severity at routing time.
_LESSON_SEVERITY_TO_SHIP: dict[str, str] = {
    "CRITICAL": "BLOCK",
    "HIGH": "HIGH",
    "MEDIUM": "MEDIUM",
    "LOW": "LOW",
}
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
# `Resolved by` vocabulary: 40-hex SHA, the literal `spec-edit` / `plan-edit`,
# or `accepted-risk:<reason>` where <reason> is any non-empty trailing text.
# Empty cells are tolerated and surface as ``resolved_by=None``; this pattern
# only runs against non-empty cells. The 40-hex form is what the trap-memory
# harvest path keys on; the literals carry no SHA so harvest skips them.
_VALID_RESOLVED_BY_PATTERN = re.compile(r"^([0-9a-f]{40}|spec-edit|plan-edit|accepted-risk:.+)$")


@dataclass(frozen=True, kw_only=True)
class ShipFinding:
    """One unresolved REVIEW.code.md finding tagged for the ship gate.

    Two kinds share this shape:

    - ``kind="article"`` (default, existing behavior): ``article_id`` carries
      an ``A<n>`` Constitution article id. ``lesson_id`` is ``None``.
    - ``kind="lesson"``: ``lesson_id`` carries an ``L<NNN>`` lesson id and
      ``article_id`` is ``None``.

    Contract (NOT enforced at construction time — mypy + tag-emission logic
    in :func:`_findings_from_row` are sufficient gates): exactly one of
    ``article_id`` / ``lesson_id`` is populated per finding. The runtime
    accepts both-None and both-set for forward compatibility, but those
    shapes never appear in normal parser output.
    """

    article_id: str | None = None
    severity: str  # BLOCK|HIGH|MEDIUM|LOW
    resolved_by: str | None = None
    location: str
    message: str
    lesson_id: str | None = None
    kind: Literal["article", "lesson"] = "article"


def _parse_table_columns(line: str) -> list[str]:
    """Split a markdown table row into trimmed cell values."""
    cells = line.strip().strip("|").split("|")
    return [c.strip() for c in cells]


def parse_review_findings(path: Path) -> list[ShipFinding]:
    """Parse REVIEW.code.md for ``Status: open`` findings tagged ``[constitution:A<n>]``.

    Resolved or accepted-risk rows are convergence-history and skipped —
    the gate acts on unresolved findings only.

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
    instead of silently bypassing the gate. The optional ``Resolved by``
    column is also tolerated: missing column → every emitted
    ``ShipFinding`` carries ``resolved_by=None``; present-but-empty cell →
    ``resolved_by=None``; present-and-populated cell must match the
    ``{40-hex SHA, spec-edit, plan-edit, accepted-risk:<reason>}``
    vocabulary or ``ShipGateError`` is raised.

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
        ShipGateError: When a row's Status, Severity, or Resolved by cell
            holds an unrecognized value.
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
    try:
        resolved_by_col = header.index("Resolved by")
    except ValueError:
        # Legacy pre-trap-memory layout: column absent, every emitted
        # ShipFinding carries ``resolved_by=None``.
        resolved_by_col = -1

    out: list[ShipFinding] = []
    for line in lines[header_idx + 1 :]:
        if not line.startswith("| F-"):
            continue
        out.extend(
            _findings_from_row(
                line,
                header=header,
                status_col=status_col,
                resolved_by_col=resolved_by_col,
                source=path,
            )
        )
    return out


def _findings_from_row(
    line: str,
    *,
    header: list[str],
    status_col: int,
    resolved_by_col: int,
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
    # for tagged rows (those are the ones that influence the gate). An
    # untagged row with an unusual Status (e.g. "in-progress" or a typo) is
    # reviewer convergence-history that this parser can ignore; validating
    # its Status would raise ShipGateError on rows the gate never cared about
    # in the first place. The Resolved by vocabulary is also gated behind
    # the tag check for the same reason — an authoring typo on an untagged
    # row must not break a parse the gate never cared about in the first
    # place. Both constitution and lesson tags qualify a row.
    tag_ids = _TAG_RE.findall(message)
    lesson_ids = _LESSON_TAG_RE.findall(message)
    if not tag_ids and not lesson_ids:
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
    # Resolved by is optional. Column absent → resolved_by=None (legacy
    # layout). Column present but cell empty → resolved_by=None. Column
    # present with content → must match the 40-hex SHA / spec-edit / plan-edit
    # / accepted-risk:<reason> vocabulary or we raise.
    resolved_by: str | None = None
    if resolved_by_col >= 0:
        resolved_by_raw = cells[resolved_by_col].strip()
        if resolved_by_raw:
            if not _VALID_RESOLVED_BY_PATTERN.match(resolved_by_raw):
                raise ShipGateError(
                    f"unrecognized Resolved by value: {resolved_by_raw!r} in {source}"
                )
            resolved_by = resolved_by_raw
    # findall keeps every tag in declaration order; one ShipFinding per tag
    # so each routes through its partitioner on its own merits. Article tags
    # are emitted first, then lesson tags — pinned by the test suite so
    # callers can rely on the ordering.
    out: list[ShipFinding] = [
        ShipFinding(
            article_id=article_id,
            severity=severity,
            resolved_by=resolved_by,
            location=location,
            message=message,
        )
        for article_id in tag_ids
    ]
    out.extend(
        ShipFinding(
            lesson_id=lesson_id,
            kind="lesson",
            severity=severity,
            resolved_by=resolved_by,
            location=location,
            message=message,
        )
        for lesson_id in lesson_ids
    )
    return out


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


def partition_by_lesson_severity(
    findings: Iterable[ShipFinding],
    lessons: Iterable[Lesson],
) -> tuple[list[ShipFinding], list[ShipFinding], list[ShipFinding]]:
    """Three-way split of lesson-tagged ``ShipFinding`` rows.

    Only findings with ``kind == "lesson"`` are considered; article-kind
    findings are silently skipped so a caller can pass the full parser output
    through both partitioners side-by-side without prefiltering.

    Routing (per the lesson's own ``Severity:`` field — source of truth):

    - ``CRITICAL`` -> gate (BLOCK-equivalent).
    - ``HIGH``     -> gate.
    - ``MEDIUM``   -> warn.
    - ``LOW``      -> info.

    Consistency check: the row's Severity cell SHOULD match
    ``_LESSON_SEVERITY_TO_SHIP[lesson.severity]``. A mismatch raises
    :class:`ShipGateError` naming both values; the reviewer subagent is
    expected to keep the cell synchronised with the lesson, and making the
    mismatch loud forces that discipline (silent drift would otherwise let
    a CRITICAL lesson route to warn because the reviewer typed ``MEDIUM``
    in the cell).

    Args:
        findings: Iterable of parsed ``ShipFinding`` rows; article-kind
            entries are filtered out at this layer.
        lessons: Loaded Lesson entries used to look up severity by
            ``lesson_id``.

    Returns:
        Tuple ``(gate, warn, info)`` of lesson-kind findings.

    Raises:
        ShipGateError: When a lesson-kind finding references a ``lesson_id``
            not present in ``lessons`` (stale tag, retired/removed lesson),
            or when the row's Severity cell disagrees with the lesson's
            Severity field after the ship-severity mapping.
    """
    by_id = {le.id: le for le in lessons}
    gate: list[ShipFinding] = []
    warn: list[ShipFinding] = []
    info: list[ShipFinding] = []
    for f in findings:
        if f.kind != "lesson":
            continue
        if f.lesson_id is None:
            # Defensive: a kind='lesson' finding without a lesson_id is a
            # constructor mis-use. Surface loudly instead of silently
            # routing to info.
            raise ShipGateError("kind='lesson' ShipFinding missing lesson_id")
        lesson = by_id.get(f.lesson_id)
        if lesson is None:
            raise ShipGateError(
                f"partition_by_lesson_severity: unknown lesson id {f.lesson_id!r} "
                f"(stale tag or retired lesson removed from .forge/intel/lessons.md)"
            )
        expected_severity = _LESSON_SEVERITY_TO_SHIP[lesson.severity]
        if f.severity != expected_severity:
            raise ShipGateError(
                f"row Severity={f.severity!r} but lesson {lesson.id} has "
                f"Severity={lesson.severity!r} (expected row Severity="
                f"{expected_severity!r})"
            )
        if lesson.severity in ("CRITICAL", "HIGH"):
            gate.append(f)
        elif lesson.severity == "MEDIUM":
            warn.append(f)
        else:
            info.append(f)
    return gate, warn, info


_INLINE_BACKTICK_RUN_RE = re.compile(r"`+")
_LESSON_TITLE_MAX_CHARS = 80


def _sanitize_for_inline_markdown(text: str, *, max_chars: int = _LESSON_TITLE_MAX_CHARS) -> str:
    """Sanitize free-form text for use inside markdown bullets or inline contexts.

    The lesson ``Trap`` field is author-controlled and may contain whitespace,
    backticks, or leading ``#`` characters that would break inline markdown
    rendering or accidentally create phantom headings inside the ACK body
    block. This helper:

    * collapses any whitespace run (including embedded newlines / tabs) into a
      single space, so a multi-line trap cannot bleed across bullet lines;
    * strips leading ``#`` characters so a title starting with ``## ...``
      cannot create a phantom heading inside the bullet list (which would in
      turn confuse ``validate_deviations``' heading-vs-cause cross-ref);
    * neutralises an odd-count backtick run so the inline code span stays
      closed;
    * truncates to ``max_chars`` with a trailing ellipsis when over budget.
    """
    cleaned = re.sub(r"\s+", " ", text).strip()
    # Strip leading hash characters and any whitespace that follows them.
    while cleaned.startswith("#"):
        cleaned = cleaned[1:].lstrip()
    backticks = _INLINE_BACKTICK_RUN_RE.findall(cleaned)
    if len(backticks) % 2 == 1:
        # Drop the trailing unbalanced run so the inline code span closes.
        idx = cleaned.rfind(backticks[-1])
        cleaned = (cleaned[:idx] + cleaned[idx + len(backticks[-1]) :]).rstrip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[: max_chars - 1].rstrip() + "…"
    return cleaned


def _lesson_title_fragment(lesson: Lesson) -> str:
    """Return a sanitised first-sentence-of-``Trap`` title for headings + bullets.

    Splits on ``". "`` and takes ``[0]``, then runs the result through
    :func:`_sanitize_for_inline_markdown` so an author-controlled Trap cannot
    disfigure decisions.md or the gate prompt with stray ``##`` headings or
    unbalanced backticks.
    """
    raw = lesson.trap.split(". ", 1)[0].rstrip(".")
    return _sanitize_for_inline_markdown(raw)


def render_gate_prompt(
    gate: list[ShipFinding],
    articles: list[Article],
    *,
    lessons: Iterable[Lesson] | None = None,
) -> str:
    """Render the ship-time gate prompt for gate-bucket findings.

    Handles both article-kind and lesson-kind findings. Pass ``lessons``
    whenever the gate bucket may contain lesson-kind entries; the rendering
    looks each lesson up by id to print its ``Trap:`` and ``Avoidance:``
    fields. ``lessons=None`` is fine when the bucket is article-only; when
    a lesson-kind finding is present without a ``lessons`` argument the
    function raises :class:`ShipGateError` so the SKILL orchestrator cannot
    silently print ``(unknown lesson)``.

    Args:
        gate: Findings bucketed into the gate partition.
        articles: Loaded Constitution articles.
        lessons: Loaded Lesson entries. Required only when the gate bucket
            contains at least one lesson-kind finding; ``None`` is accepted
            for the article-only legacy path.

    Returns:
        Multiline string suitable for printing to the user. Empty string when
        ``gate`` is empty.

    Raises:
        ShipGateError: When a gate-bucket article-kind finding references an
            id absent from ``articles`` (defense in depth — the partitioner
            already routes unknown ids to info), when a lesson-kind finding
            is present but ``lessons=None``, or when a lesson-kind finding
            references a lesson id absent from the supplied ``lessons``.
    """
    if not gate:
        return ""
    by_id = {a.id: a for a in articles}
    lesson_by_id = {le.id: le for le in lessons} if lessons is not None else None
    unknown_articles = sorted(
        {
            f.article_id
            for f in gate
            if f.kind == "article" and f.article_id and f.article_id not in by_id
        }
    )
    if unknown_articles:
        raise ShipGateError(
            f"render_gate_prompt: unknown article id(s) in gate bucket: {unknown_articles}"
        )
    if any(f.kind == "lesson" for f in gate) and lesson_by_id is None:
        raise ShipGateError(
            "render_gate_prompt: gate contains lesson-kind findings but no `lessons` argument"
        )
    unknown_lessons = sorted(
        {
            f.lesson_id
            for f in gate
            if f.kind == "lesson" and f.lesson_id and f.lesson_id not in (lesson_by_id or {})
        }
    )
    if unknown_lessons:
        raise ShipGateError(
            f"render_gate_prompt: unknown lesson id(s) in gate bucket: {unknown_lessons}"
        )
    lines = [
        "=" * 57,
        "  SHIP-GATE FINDINGS - UNRESOLVED AT SHIP",
        "=" * 57,
        "",
        f"The reviewer flagged {len(gate)} finding(s) against project Constitution",
        "articles or trap-memory lessons. The gate does not BLOCK - you are the gate.",
        "",
    ]
    for f in gate:
        if f.kind == "lesson":
            # lesson_by_id is guaranteed non-None here (validation above);
            # the local rebinding pins the type for mypy.
            assert_lesson_by_id = lesson_by_id or {}
            lesson = assert_lesson_by_id[f.lesson_id or ""]
            title = _lesson_title_fragment(lesson)
            avoidance = lesson.avoidance.split(". ", 1)[0].rstrip(".")
            lines.append(f'[lesson:{f.lesson_id}] {f.severity} (lesson: "{title}")')
            lines.append(f"  File: {f.location}")
            lines.append(f"  Reviewer note: {f.message}")
            lines.append(f"  Trap: {lesson.trap}")
            lines.append(f"  Avoidance: {avoidance}")
            lines.append("")
            continue
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
    *,
    lessons: Iterable[Lesson] | None = None,
) -> str:
    """Render the SHOULD-level / MEDIUM-lesson advisory summary.

    Handles both article-kind and lesson-kind findings. Pass ``lessons``
    whenever the warn bucket may contain lesson-kind entries; otherwise a
    lesson-kind finding raises :class:`ShipGateError` rather than print a
    placeholder.

    Args:
        warn: Findings bucketed into the warn partition.
        articles: Loaded Constitution articles.
        lessons: Loaded Lesson entries. Required only when the warn bucket
            contains at least one lesson-kind finding.

    Returns:
        Multiline summary string. Empty string when ``warn`` is empty.

    Raises:
        ShipGateError: When a lesson-kind finding is present but ``lessons``
            is ``None``, or when a lesson-kind finding references a lesson
            id absent from the supplied ``lessons``.
    """
    if not warn:
        return ""
    by_id = {a.id: a for a in articles}
    lesson_by_id = {le.id: le for le in lessons} if lessons is not None else None
    if any(f.kind == "lesson" for f in warn) and lesson_by_id is None:
        raise ShipGateError(
            "render_warn_summary: warn contains lesson-kind findings but no `lessons` argument"
        )
    unknown_lessons = sorted(
        {
            f.lesson_id
            for f in warn
            if f.kind == "lesson" and f.lesson_id and f.lesson_id not in (lesson_by_id or {})
        }
    )
    if unknown_lessons:
        raise ShipGateError(
            f"render_warn_summary: unknown lesson id(s) in warn bucket: {unknown_lessons}"
        )
    lines = ["Ship-gate advisory findings:"]
    for f in warn:
        if f.kind == "lesson":
            # lesson_by_id is non-None here (validation above); rebind for
            # mypy without using `assert` (ruff S101).
            assert_lesson_by_id = lesson_by_id or {}
            lesson = assert_lesson_by_id[f.lesson_id or ""]
            title = _lesson_title_fragment(lesson)
            lines.append(
                f"  - [lesson:{f.lesson_id}] {f.severity} {f.location} — {title}: {f.message}"
            )
            continue
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
    lessons: Iterable[Lesson] | None = None,
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
        gate_findings: Findings the user explicitly ACKNOWLEDGED. May mix
            article-kind and lesson-kind entries; both are recorded in the
            same decisions.md heading with their respective tag prefixes.
        articles: Loaded Constitution articles (for title lookup).
        lessons: Loaded Lesson entries (for title lookup on lesson-kind
            acknowledgements). Required only when ``gate_findings`` contains
            at least one lesson-kind entry.
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
    lesson_by_id = {le.id: le for le in lessons} if lessons is not None else {}
    if any(f.kind == "lesson" for f in gate_findings) and not lesson_by_id:
        raise ShipGateError(
            "make_acknowledgement_hook: gate_findings contains lesson-kind entries "
            "but no `lessons` argument was supplied"
        )

    cause_tags = [
        f"[lesson:{f.lesson_id}]" if f.kind == "lesson" else f"[constitution:{f.article_id}]"
        for f in gate_findings
    ]
    cause = _ACK_PREFIX + ": " + ", ".join(cause_tags)

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
                if f.kind == "lesson":
                    lesson = lesson_by_id.get(f.lesson_id or "")
                    title = _lesson_title_fragment(lesson) if lesson else "(unknown)"
                    # Strip every [lesson:L<NNN>] from the reviewer message so
                    # tag echoes do not pile up alongside the bullet's own
                    # leading tag, even when a row carried duplicate tags.
                    clean_message = _LESSON_TAG_RE.sub("", f.message).lstrip(" -")
                    body_lines.append(
                        f"- [lesson:{f.lesson_id}] **{title}** — {f.location} — {clean_message}"
                    )
                    continue
                article = by_id.get(f.article_id or "")
                title = article.title if article else "(unknown)"
                # Strip every [constitution:A<n>] from the reviewer message so
                # duplicate tag mentions do not double-echo onto the bullet.
                clean_message = _TAG_RE.sub("", f.message).lstrip(" -")
                body_lines.append(
                    f"- [constitution:{f.article_id}] **{title}** — {f.location} — {clean_message}"
                )
            # Atomic-replace the whole file instead of streaming the append.
            # A raw ``open("a") + write`` is not atomic for payloads above
            # PIPE_BUF (~4 KiB on Linux), so a crash mid-write could leave a
            # partial heading on disk; the idempotency check would then either
            # double-append (when the partial fell short of the ``Cause:``
            # line) or assume the entry landed when only its prefix did.
            current_text = decisions_path.read_text(encoding="utf-8")
            atomic_replace(decisions_path, current_text + "\n".join(body_lines) + "\n")

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


# --- git-conventions wiring -----------------------------------------------
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

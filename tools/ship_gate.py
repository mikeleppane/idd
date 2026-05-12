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
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from tools.constitution import Article
from tools.constitution_amend import atomic_replace, ensure_decisions_file
from tools.validate import Finding
from tools.validate.git_conventions import validate_git_conventions

if TYPE_CHECKING:
    from tools.intel.lessons import Lesson


class ShipGateError(RuntimeError):
    """Raised when the gate cannot record the acknowledgement state."""


_TAG_RE = re.compile(r"\[constitution:(A\d+)\]")
# Lesson ids are zero-padded 3-digit by parser contract. Match the same shape
# at the tag level so a malformed [lesson:L1] surfaces as 'no lesson tag'
# (and the row falls out of the gate's scope entirely) instead of becoming
# a bewildering 'unknown lesson id' error from the partitioner downstream.
_LESSON_TAG_RE = re.compile(r"\[lesson:(L\d{3})\]")
# Lesson severity vocabulary {CRITICAL, HIGH, MEDIUM, LOW} maps to the ship-
# gate severity vocabulary {BLOCK, HIGH, MEDIUM, LOW}. Only ``CRITICAL`` needs
# renaming; the other three pass through unchanged, so the dict carries the
# rename entry only and ``_lesson_to_ship_severity`` falls back to identity
# for everything else. The reviewer copies the result into the REVIEW.md
# Severity cell so the ship-gate parser can keep treating the Severity cell
# as the closed _VALID_SEVERITY_VALUES vocabulary; the partitioner cross-
# checks the cell against the lesson's source-of-truth Severity at routing
# time.
_LESSON_SEVERITY_RENAME: dict[str, str] = {"CRITICAL": "BLOCK"}

# Translate REVIEW.md row severity to Lesson severity for trap-memory harvest.
# REVIEW rows use the closed BLOCK/HIGH/MEDIUM/LOW vocabulary; Lessons use
# CRITICAL/HIGH/MEDIUM/LOW. CRITICAL is the lesson analog of BLOCK; the other
# three names pass through identically. Centralizing the map here lets the
# forge-review SKILL prose reference one symbol instead of inlining the
# translation table — a future vocabulary change touches one location.
_REVIEW_TO_LESSON_SEVERITY: dict[str, str] = {
    "BLOCK": "CRITICAL",
    "HIGH": "HIGH",
    "MEDIUM": "MEDIUM",
    "LOW": "LOW",
}


def _lesson_to_ship_severity(lesson_severity: str) -> str:
    """Translate a lesson Severity value into the ship-gate Severity cell."""
    return _LESSON_SEVERITY_RENAME.get(lesson_severity, lesson_severity)


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
# or `accepted-risk:<reason>` where <reason> is any non-empty trailing text,
# bounded to 200 chars so a runaway cell cannot inflate the in-memory finding.
# Empty cells are tolerated and surface as ``resolved_by=None``; this pattern
# only runs against non-empty cells. The 40-hex form is what the trap-memory
# harvest path keys on; the literals carry no SHA so harvest skips them.
_VALID_RESOLVED_BY_PATTERN = re.compile(
    r"^([0-9a-f]{40}|spec-edit|plan-edit|accepted-risk:.{1,200})$"
)
# 40-hex SHA detector used to gate the case-normalization branch in
# ``_findings_from_row``. The full vocabulary above is case-sensitive by
# design (the literals must match verbatim); only the SHA form gets
# lowercased before re-running the vocabulary regex.
_HEX_40_RE = re.compile(r"^[0-9a-fA-F]{40}$")

# Refuse to parse a REVIEW file larger than this cap. A typical REVIEW.code.md
# holds a couple of dozen findings; 1 MiB is several orders of magnitude past
# plausible content and guards against an accidental (or hostile) huge file
# from blowing up the splitlines + per-row regex passes.
_MAX_REVIEW_FILE_BYTES = 1 << 20


@dataclass(frozen=True, kw_only=True)
class HarvestCandidate:
    """A REVIEW.code.md row eligible for trap-memory harvest.

    The forge-review harvest sub-step iterates these rows and surfaces a
    user prompt offering to capture the row as a Lesson for future
    subagents. Lesson construction (severity translation via
    :data:`_REVIEW_TO_LESSON_SEVERITY`, tag drafting against
    ``tools.intel.lessons._TAG_VOCAB``, body composition) happens
    skill-side; this helper only emits the structured row so the SKILL
    prose never reaches into REVIEW.code.md with its own parser.

    Sharing :func:`_iter_review_rows` with :func:`parse_review_findings`
    keeps the two public consumers locked to one parsing path — a
    REVIEW.md template column-rename touches one helper instead of
    silently drifting between the two parsers.
    """

    row_id: str
    severity: str  # closed vocab: BLOCK | HIGH | MEDIUM | LOW
    resolved_by: str  # 40-hex SHA, lowercase-normalized at parse time
    location: str
    problem: str  # raw Problem cell text (still carries any tag markers)
    recommended_fix: str
    article_tags: tuple[str, ...]
    lesson_tags: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class _RowRecord:
    """Internal: all cells from one Findings-table row plus parsed tags.

    Shared by :func:`parse_review_findings` and
    :func:`parse_review_findings_for_harvest`. Owns the cell projection,
    closed-vocabulary validation, and tag extraction so the two public
    parsers stay thin filters over a single row stream.
    """

    id: str
    severity: str
    status: str
    resolved_by: str | None
    location: str
    problem: str
    recommended_fix: str
    source: str
    article_tags: tuple[str, ...]
    lesson_tags: tuple[str, ...]


@dataclass(frozen=True, kw_only=True)
class ShipFinding:
    """One unresolved REVIEW.code.md finding tagged for the ship gate.

    Exactly one of ``article_id`` / ``lesson_id`` is populated per finding:

    * Article finding — ``article_id`` carries an ``A<n>`` Constitution
      article id; ``lesson_id`` is ``None``.
    * Lesson finding — ``lesson_id`` carries an ``L<NNN>`` lesson id;
      ``article_id`` is ``None``.

    The invariant is enforced at construction time via :meth:`__post_init__`
    so callers cannot smuggle a both-None / both-set finding into the
    routing path. Use :attr:`is_article` / :attr:`is_lesson` for branch
    checks at call sites instead of reading the optional id fields.
    """

    article_id: str | None = None
    severity: str  # BLOCK|HIGH|MEDIUM|LOW
    # ``resolved_by`` carries the 40-hex SHA, the literal ``spec-edit`` /
    # ``plan-edit``, or ``accepted-risk:<reason>`` from the REVIEW.md cell.
    # SHA values are normalized to lowercase at parse time so downstream
    # comparisons stay deterministic regardless of whether the reviewer
    # pasted upper- or mixed-case hex from a git GUI; the literal forms are
    # case-sensitive and stored verbatim.
    resolved_by: str | None = None
    location: str
    message: str
    lesson_id: str | None = None

    def __post_init__(self) -> None:
        """Enforce exactly-one-id at construction time."""
        has_article = self.article_id is not None
        has_lesson = self.lesson_id is not None
        if has_article == has_lesson:
            raise ShipGateError(
                "ShipFinding requires exactly one of article_id / lesson_id; "
                f"got article_id={self.article_id!r}, lesson_id={self.lesson_id!r}"
            )

    @property
    def is_article(self) -> bool:
        """True iff this is a Constitution-article finding."""
        return self.article_id is not None

    @property
    def is_lesson(self) -> bool:
        """True iff this is a trap-memory lesson finding."""
        return self.lesson_id is not None


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
    out: list[ShipFinding] = []
    for row in _iter_review_rows(path):
        if row.status != "open":
            continue
        # findall ordering: article tags first, then lesson tags. Pinned by
        # the test suite so callers can rely on the ordering across both
        # public parsers.
        out.extend(
            ShipFinding(
                article_id=article_id,
                severity=row.severity,
                resolved_by=row.resolved_by,
                location=row.location,
                message=row.problem,
            )
            for article_id in row.article_tags
        )
        out.extend(
            ShipFinding(
                lesson_id=lesson_id,
                severity=row.severity,
                resolved_by=row.resolved_by,
                location=row.location,
                message=row.problem,
            )
            for lesson_id in row.lesson_tags
        )
    return out


def parse_review_findings_for_harvest(path: Path) -> list[HarvestCandidate]:
    """Emit harvest candidates from REVIEW.code.md for the forge-review skill.

    A row qualifies as a candidate iff:

        1. ``Status`` cell is ``resolved`` (case-insensitive).
        2. ``Resolved by`` cell holds a 40-hex SHA — not ``manual``,
           ``spec-edit``, ``plan-edit``, or ``accepted-risk:<reason>``.
        3. ``Severity`` cell is ``BLOCK`` or ``HIGH``.

    The harvest sub-step (`skills/forge-review/SKILL.md`) consumes this
    list and prompts the user via one ``AskUserQuestion`` per candidate.
    Filtering accepted-risk / spec-edit / plan-edit at the parser keeps
    the SKILL prose free of vocabulary checks — a future ``Resolved by``
    vocabulary change touches one regex in one helper.

    Shared parsing path: :func:`_iter_review_rows` produces one
    :class:`_RowRecord` per Findings-table row with cells projected and
    closed-vocab validated. Both this function and
    :func:`parse_review_findings` filter+project that same stream, so a
    REVIEW.md template column-rename surfaces in one place — no silent
    drift between the two consumers.

    Missing file returns ``[]``. A REVIEW.code.md with no ``# Findings``
    heading or no qualifying rows also returns ``[]``. Closed-vocabulary
    typos (Status, Severity, or Resolved by) raise
    :class:`ShipGateError` — matches the strictness contract of
    :func:`parse_review_findings`.

    Args:
        path: Path to ``REVIEW.code.md``.

    Returns:
        List of :class:`HarvestCandidate` records, one per qualifying
        row, in document order.

    Raises:
        ShipGateError: When any row's Status, Severity, or Resolved by
            cell holds an unrecognized closed-vocabulary value.
    """
    out: list[HarvestCandidate] = []
    for row in _iter_review_rows(path):
        if row.status != "resolved":
            continue
        if row.resolved_by is None or not _HEX_40_RE.match(row.resolved_by):
            continue
        if row.severity not in ("BLOCK", "HIGH"):
            continue
        out.append(
            HarvestCandidate(
                row_id=row.id,
                severity=row.severity,
                resolved_by=row.resolved_by,
                location=row.location,
                problem=row.problem,
                recommended_fix=row.recommended_fix,
                article_tags=row.article_tags,
                lesson_tags=row.lesson_tags,
            )
        )
    return out


def _iter_review_rows(path: Path) -> Iterator[_RowRecord]:
    """Stream parsed rows from REVIEW.code.md as :class:`_RowRecord` records.

    Shared by :func:`parse_review_findings` (filters Status: open) and
    :func:`parse_review_findings_for_harvest` (filters Status: resolved
    + SHA-shaped Resolved by + HIGH/BLOCK severity). Owns table-header
    location, column-index lookup, cell projection, closed-vocab
    validation, and tag extraction so the two public parsers stay thin
    filters over a single row stream.

    Yields rows in document order. Skips structurally malformed rows
    (short cell counts, missing required columns) silently to match the
    mid-edit REVIEW.code.md tolerance documented on
    :func:`parse_review_findings`.

    Tag check gates closed-vocab validation: an untagged row with an
    unusual Status / Severity / Resolved by is treated as
    convergence-history this parser can ignore (matches pre-refactor
    behavior).

    Args:
        path: Path to REVIEW.code.md.

    Yields:
        One :class:`_RowRecord` per qualifying ``| F-...`` row.

    Raises:
        ShipGateError: When the file exceeds
            :data:`_MAX_REVIEW_FILE_BYTES`, or when a tagged row carries
            an unrecognized closed-vocabulary value.
    """
    if not path.exists():
        return

    size = path.stat().st_size
    if size > _MAX_REVIEW_FILE_BYTES:
        raise ShipGateError(
            f"REVIEW file at {path} is {size} bytes; refuse to parse "
            f"a file larger than {_MAX_REVIEW_FILE_BYTES} bytes "
            "(suspected malformed or out-of-scope content)"
        )
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # Anchor to the `# Findings` heading first so a `| ID | ... |` table
    # in the preamble (inventory, ToC, column legend) cannot disarm the
    # parser. Without the heading, yield nothing.
    findings_heading_idx = next(
        (i for i, line in enumerate(lines) if _FINDINGS_HEADING_RE.match(line)),
        None,
    )
    if findings_heading_idx is None:
        return
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
        return
    header = _parse_table_columns(lines[header_idx])
    columns = _column_layout(header)

    for line in lines[header_idx + 1 :]:
        if not line.startswith("| F-"):
            continue
        record = _row_record_from_line(line, header=header, columns=columns, source=path)
        if record is None:
            continue
        yield record


@dataclass(frozen=True, kw_only=True)
class _ColumnLayout:
    """Resolved column indices for the Findings table.

    A ``-1`` value marks an absent column tolerated by the parser
    (legacy layout). Built once per file and reused across every row so
    a column-rename touches one helper instead of every row body.
    """

    id_col: int
    severity_col: int
    status_col: int
    resolved_by_col: int
    location_col: int
    problem_col: int
    recommended_fix_col: int
    source_col: int


def _column_layout(header: list[str]) -> _ColumnLayout:
    """Resolve column indices from a parsed header row.

    Required columns missing → corresponding row will be skipped at
    projection time (defensive; in practice the template guarantees the
    required set). Optional columns missing → field defaults at the
    record layer (``resolved_by=None``, ``status="open"`` legacy).
    """

    def _idx(name: str) -> int:
        try:
            return header.index(name)
        except ValueError:
            return -1

    return _ColumnLayout(
        id_col=_idx("ID"),
        severity_col=_idx("Severity"),
        status_col=_idx("Status"),
        resolved_by_col=_idx("Resolved by"),
        location_col=_idx("Location"),
        problem_col=_idx("Problem"),
        recommended_fix_col=_idx("Recommended Fix"),
        source_col=_idx("Source"),
    )


def _row_record_from_line(
    line: str,
    *,
    header: list[str],
    columns: _ColumnLayout,
    source: Path,
) -> _RowRecord | None:
    """Project a single ``| F-...`` row into a :class:`_RowRecord`.

    Returns ``None`` for structurally malformed rows (insufficient
    cells, missing required column). Raises :class:`ShipGateError` for
    closed-vocab typos on tagged rows. The tag check gates vocab
    validation so an untagged convergence-history row with an unusual
    cell value is ignored (matches pre-refactor behavior).
    """
    cells = _parse_table_columns(line)
    if len(cells) < len(header):
        return None
    if columns.severity_col < 0 or columns.location_col < 0 or columns.problem_col < 0:
        return None
    severity = cells[columns.severity_col]
    location = cells[columns.location_col]
    problem = cells[columns.problem_col]
    # Tag check FIRST. Closed Status / Severity / Resolved-by vocabularies
    # only matter for tagged rows — an untagged convergence-history row with
    # an unusual cell value must not raise.
    article_tags = tuple(_TAG_RE.findall(problem))
    lesson_tags = tuple(_LESSON_TAG_RE.findall(problem))
    if not article_tags and not lesson_tags:
        return None
    # Status — closed vocabulary; legacy layout (column absent) treats every
    # row as ``open``.
    if columns.status_col >= 0:
        raw_status = cells[columns.status_col]
        normalized_status = raw_status.lower()
        if normalized_status not in _VALID_STATUS_VALUES:
            raise ShipGateError(f"unrecognized Status value: {raw_status!r} in {source}")
        status = normalized_status
    else:
        status = "open"
    # Severity — closed 4-value enum from the REVIEW.md template.
    if severity not in _VALID_SEVERITY_VALUES:
        raise ShipGateError(f"unrecognized Severity value: {severity!r} in {source}")
    # Resolved by — optional column; SHA values lowercase-normalized.
    resolved_by: str | None = None
    if columns.resolved_by_col >= 0:
        resolved_by_raw = cells[columns.resolved_by_col].strip()
        if resolved_by_raw:
            normalized = (
                resolved_by_raw.lower() if _HEX_40_RE.match(resolved_by_raw) else resolved_by_raw
            )
            if not _VALID_RESOLVED_BY_PATTERN.match(normalized):
                raise ShipGateError(
                    f"unrecognized Resolved by value: {resolved_by_raw!r} in {source}"
                )
            resolved_by = normalized
    row_id = cells[columns.id_col] if columns.id_col >= 0 else ""
    recommended_fix = cells[columns.recommended_fix_col] if columns.recommended_fix_col >= 0 else ""
    row_source = cells[columns.source_col] if columns.source_col >= 0 else ""
    return _RowRecord(
        id=row_id,
        severity=severity,
        status=status,
        resolved_by=resolved_by,
        location=location,
        problem=problem,
        recommended_fix=recommended_fix,
        source=row_source,
        article_tags=article_tags,
        lesson_tags=lesson_tags,
    )


class _PartitionResult(tuple[list[ShipFinding], list[ShipFinding], list[ShipFinding]]):
    """Three-bucket partition tuple with a sidecar ``routing_warnings`` channel.

    Subclasses :class:`tuple` so existing callers can still unpack
    ``gate, warn, info = partition_by_...(findings, ...)`` byte-equal with the
    legacy contract. The sidecar field carries diagnostic strings for
    typo-class issues (unknown lesson id, unknown article id) that pre-fix
    raised ``ShipGateError`` and blocked ship outright. Real configuration
    bugs (Severity-mismatch) still raise — they reach a different code path
    inside :func:`partition_by_lesson_severity`.

    The class is private; access ``routing_warnings`` via the module-level
    :func:`routing_warnings` helper instead of touching the attribute
    directly so the seam stays stable across future refactors. ``__slots__``
    is intentionally omitted — Python forbids non-empty ``__slots__`` on
    :class:`tuple` subclasses, so the attribute lives on the per-instance
    dict.
    """

    def __new__(
        cls,
        gate: list[ShipFinding],
        warn: list[ShipFinding],
        info: list[ShipFinding],
        *,
        routing_warnings: tuple[str, ...] = (),
    ) -> _PartitionResult:
        # Build via tuple.__new__ so identity-equality with a plain three-tuple
        # of the same lists holds (existing tests compare directly).
        self = super().__new__(cls, (gate, warn, info))
        self._routing_warnings = routing_warnings
        return self

    _routing_warnings: tuple[str, ...]


def routing_warnings(
    partition: tuple[list[ShipFinding], list[ShipFinding], list[ShipFinding]],
) -> tuple[str, ...]:
    """Return the diagnostic ``routing_warnings`` channel for a partition.

    The channel is populated when :func:`partition_by_article_level` or
    :func:`partition_by_lesson_severity` encountered an unknown tag id — a
    stray ``[lesson:L042]`` whose lesson is absent or retired, or a stale
    ``[constitution:A99]`` after an article was renamed. The synthetic info
    finding routes the row past the gate so ship can proceed; this channel
    surfaces the typo so the user can clean it up at leisure.

    A plain three-tuple result (e.g. a future caller building a partition by
    hand) returns ``()``. Real configuration bugs (Severity-mismatch between
    a row's Severity cell and the lesson's source-of-truth Severity field)
    still raise ``ShipGateError`` upstream of this function.
    """
    return getattr(partition, "_routing_warnings", ())


def partition_by_article_level(
    findings: Iterable[ShipFinding],
    articles: list[Article],
) -> _PartitionResult:
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
        :class:`_PartitionResult` — a tuple ``(gate, warn, info)`` carrying
        an additional ``routing_warnings`` channel surfaced via the
        :func:`routing_warnings` helper:

            - ``gate``: article level == ``CRITICAL``.
            - ``warn``: article level == ``SHOULD``.
            - ``info``: article level == ``MAY``, plus findings whose
              article id is not present in ``articles`` (unknown article).
            - ``routing_warnings``: one diagnostic string per unknown
              article id, naming the id and source location so the user can
              fix the typo at leisure.

    Symmetric with :func:`partition_by_lesson_severity`: both partitioners
    route unknown ids to ``info`` and surface a non-blocking diagnostic via
    ``routing_warnings`` instead of raising. The article path additionally
    routes findings whose article exists but is rated ``MAY`` to ``info``
    without a warning — those are clean routes, not typos.
    """
    by_id = {a.id: a for a in articles}
    gate: list[ShipFinding] = []
    warn: list[ShipFinding] = []
    info: list[ShipFinding] = []
    warnings: list[str] = []
    for f in findings:
        article = by_id.get(f.article_id) if f.article_id else None
        if article is None:
            info.append(f)
            if f.article_id is not None:
                # Surface the typo so the user can clean it up; do NOT block
                # ship — articles get renamed during convergence often enough
                # that a stale tag is benign cleanup work, not a real bug.
                warnings.append(
                    f"unknown article id {f.article_id!r} at {f.location} "
                    f"(tag may be a typo or reference an article renamed during "
                    f"convergence)"
                )
            continue
        if article.level == "CRITICAL":
            gate.append(f)
        elif article.level == "SHOULD":
            warn.append(f)
        else:
            info.append(f)
    return _PartitionResult(gate, warn, info, routing_warnings=tuple(warnings))


def partition_by_lesson_severity(
    findings: Iterable[ShipFinding],
    lessons: Iterable[Lesson],
) -> _PartitionResult:
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
    ``_lesson_to_ship_severity(lesson.severity)``. A mismatch is a real
    configuration bug — the reviewer subagent must keep the cell synchronised
    with the lesson, and a silent drift would let a CRITICAL lesson route to
    warn because the reviewer typed ``MEDIUM`` in the cell. Severity-mismatch
    therefore still raises :class:`ShipGateError` (loud, blocking, demands a
    fix-then-reship).

    Unknown-id handling is DIFFERENT: a stray ``[lesson:L042]`` whose lesson
    is missing (typo, retired entry filtered out of the active set, paste
    error) downgrades to a synthetic LOW finding in the ``info`` bucket plus
    a non-blocking diagnostic on the ``routing_warnings`` channel. Pre-fix
    this raised ``ShipGateError`` and blocked the user from reaching ACK;
    the downgrade lets ship proceed while still surfacing the typo so the
    user can clean it up at leisure.

    Args:
        findings: Iterable of parsed ``ShipFinding`` rows; article-kind
            entries are filtered out at this layer.
        lessons: Loaded Lesson entries used to look up severity by
            ``lesson_id``.

    Returns:
        :class:`_PartitionResult` — tuple ``(gate, warn, info)`` of
        lesson-kind findings, plus a ``routing_warnings`` channel surfaced
        via :func:`routing_warnings` (one diagnostic per unknown lesson id).

    Raises:
        ShipGateError: When the row's Severity cell disagrees with the
            lesson's Severity field after the ship-severity mapping (real
            config bug, not a typo). Unknown lesson ids do NOT raise — they
            route to ``info`` and surface via ``routing_warnings``.
    """
    by_id = {le.id: le for le in lessons}
    gate: list[ShipFinding] = []
    warn: list[ShipFinding] = []
    info: list[ShipFinding] = []
    # Two channels: ``routing_errors`` for real configuration bugs that still
    # raise; ``warnings`` for typo-class issues that downgrade to info plus a
    # diagnostic. Pre-fix unknown-id joined routing_errors and blocked ship;
    # now it joins ``warnings`` and ship proceeds.
    routing_errors: list[str] = []
    warnings: list[str] = []
    for f in findings:
        if not f.is_lesson:
            continue
        # ShipFinding.__post_init__ guarantees lesson_id is not None when
        # is_lesson is True, so the lookup is safe without a re-check.
        assert f.lesson_id is not None  # noqa: S101 — typing aid for mypy
        lesson = by_id.get(f.lesson_id)
        if lesson is None:
            # Synthetic LOW finding in info bucket — preserves the row's
            # location so the user can find the typo, and keeps the original
            # tag in the message body so REVIEW.md grep locates it.
            synthetic = ShipFinding(
                lesson_id=f.lesson_id,
                severity="LOW",
                location=f.location,
                message=(
                    f"unknown lesson id {f.lesson_id!r} — tag may be a typo "
                    f"or reference a removed lesson (original row message: "
                    f"{f.message})"
                ),
                resolved_by=f.resolved_by,
            )
            info.append(synthetic)
            warnings.append(
                f"unknown lesson id {f.lesson_id!r} at {f.location} "
                f"(stale tag or retired lesson removed from .forge/intel/lessons.md)"
            )
            continue
        expected_severity = _lesson_to_ship_severity(lesson.severity)
        if f.severity != expected_severity:
            routing_errors.append(
                f"row Severity={f.severity!r} at {f.location} disagrees with lesson "
                f"{lesson.id} Severity={lesson.severity!r} (expected row Severity="
                f"{expected_severity!r})"
            )
            continue
        if lesson.severity in ("CRITICAL", "HIGH"):
            gate.append(f)
        elif lesson.severity == "MEDIUM":
            warn.append(f)
        else:
            info.append(f)
    if routing_errors:
        bullets = "\n  - ".join(routing_errors)
        raise ShipGateError(
            f"partition_by_lesson_severity: {len(routing_errors)} row(s) failed routing:\n"
            f"  - {bullets}"
        )
    return _PartitionResult(gate, warn, info, routing_warnings=tuple(warnings))


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
        {f.article_id for f in gate if f.is_article and f.article_id and f.article_id not in by_id}
    )
    if unknown_articles:
        raise ShipGateError(
            f"render_gate_prompt: unknown article id(s) in gate bucket: {unknown_articles}"
        )
    if any(f.is_lesson for f in gate) and lesson_by_id is None:
        raise ShipGateError(
            "render_gate_prompt: gate contains lesson-kind findings but no `lessons` argument"
        )
    unknown_lessons = sorted(
        {
            f.lesson_id
            for f in gate
            if f.is_lesson and f.lesson_id and f.lesson_id not in (lesson_by_id or {})
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
        if f.is_lesson:
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
    if any(f.is_lesson for f in warn) and lesson_by_id is None:
        raise ShipGateError(
            "render_warn_summary: warn contains lesson-kind findings but no `lessons` argument"
        )
    unknown_lessons = sorted(
        {
            f.lesson_id
            for f in warn
            if f.is_lesson and f.lesson_id and f.lesson_id not in (lesson_by_id or {})
        }
    )
    if unknown_lessons:
        raise ShipGateError(
            f"render_warn_summary: unknown lesson id(s) in warn bucket: {unknown_lessons}"
        )
    lines = ["Ship-gate advisory findings:"]
    for f in warn:
        if f.is_lesson:
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
#
# The prefix reads "Ship-gate finding ..." (covers both constitution-article
# and lesson-kind acknowledgements). The earlier "Constitution finding ..."
# wording was misleading for lesson-kind ACK rows, where no Constitution
# article is involved; the body's ``Cause:`` line still carries the actual
# ``[constitution:A<n>]`` / ``[lesson:L<NNN>]`` tag so the cross-ref locates
# the heading via the body group regardless of the title's content.
_ACK_PREFIX = "Ship-gate finding acknowledged at ship"


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
    if any(f.is_lesson for f in gate_findings) and not lesson_by_id:
        raise ShipGateError(
            "make_acknowledgement_hook: gate_findings contains lesson-kind entries "
            "but no `lessons` argument was supplied"
        )

    cause_tags = [
        f"[lesson:{f.lesson_id}]" if f.is_lesson else f"[constitution:{f.article_id}]"
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
                # Strip BOTH tag regexes regardless of the finding's kind so a
                # mixed-tag REVIEW row (e.g. one row carrying both
                # ``[constitution:A2]`` and ``[lesson:L007]``) cannot leak the
                # OTHER tag into the bullet body. The bullet's own leading tag
                # is rebuilt from ``f.article_id`` / ``f.lesson_id`` after the
                # strip, so duplicate or cross-kind tag echoes are removed
                # symmetrically.
                clean_message = _TAG_RE.sub("", _LESSON_TAG_RE.sub("", f.message)).strip(" -")
                if f.is_lesson:
                    lesson = lesson_by_id.get(f.lesson_id or "")
                    title = _lesson_title_fragment(lesson) if lesson else "(unknown)"
                    body_lines.append(
                        f"- [lesson:{f.lesson_id}] **{title}** — {f.location} — {clean_message}"
                    )
                    continue
                article = by_id.get(f.article_id or "")
                title = article.title if article else "(unknown)"
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
_GIT_CONV_INFO_HEADER = "Git-convention findings (informational):"


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


def render_git_conventions_info_summary(partition: GitConventionGatePartition) -> str:
    """Render LOW / WARN / INFO findings as an informational summary line block.

    Empty info bucket returns the empty string — the common case, since LOW
    / WARN / INFO findings are rare in normal commit hygiene. One line per
    finding when populated, structure mirrors
    :func:`render_git_conventions_warn_summary` so diagnostic CLI tools and
    skill prose can render either with a single visual rhythm. The info
    bucket has no consumer in the ship-time gate proper (info findings do
    not block ship and are not surfaced via the ACK prompt); this renderer
    exists so a future ``forge-ship`` skill prose update or a diagnostic
    CLI inspector can fold info findings into its output without inventing
    a parallel renderer.

    Args:
        partition: A partition produced by :func:`partition_git_conventions`.

    Returns:
        Multiline string, or ``""`` when the info bucket is empty.
    """
    if not partition.info:
        return ""
    lines = [_GIT_CONV_INFO_HEADER]
    lines.extend(f"- {f.message}  [{f.severity}]" for f in partition.info)
    return "\n".join(lines)

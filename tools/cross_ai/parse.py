"""External reviewer Markdown response → typed ``Finding`` rows.

The dispatcher mandates an explicit Markdown table format in every
prompt (see ``tools.cross_ai.prompt._REVIEWER_MANDATE``); this module is
the inverse — it ingests whatever Markdown the reviewer returned and
extracts the table back into structured rows the rest of the substrate
can reason about.

Tolerant on the way in, strict on the way out:

* Explanatory paragraphs, hidden columns, header-row whitespace, and
  trailing-pipe styling all flow through. We scan for the first header
  row that mentions every required column (``ID``, ``Severity``,
  ``Status``, ``Location``, ``Problem``, ``Fix``) and parse from there.
* The ``severity`` and ``status`` cells are passed through verbatim —
  the parser is a vocabulary-neutral pipe so unknown reviewer terms
  reach the caller intact. ``Severity`` lives here as a documentation
  vocabulary (a ``StrEnum``), **not** a type constraint on the dataclass.
* The ``source`` cell is always overwritten to ``external-<reviewer_id>``
  regardless of what the reviewer emits. Constitution tags belong in the
  Problem column; the Source column is reserved for dispatcher routing.
* Constitution tags such as ``[constitution:A1]`` in the Problem column
  pass through verbatim. The dispatcher uses them to route findings
  back to the originating Article; any reformatting breaks the link.
* When no table is found, or the first candidate table fails the
  required-column check, the parser returns an empty tuple and emits a
  ``ParseWarning`` so the caller can surface the missing-table
  condition to the operator instead of silently treating the absence
  as a clean review.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass
from enum import StrEnum

# Required columns the parser must locate in the header row before
# accepting a table as a findings table. Match is case-insensitive and
# tolerant of extra columns; ``source`` is intentionally excluded so a
# reviewer that omits it (the parser overrides it anyway) still parses.
_REQUIRED_COLUMNS: tuple[str, ...] = (
    "id",
    "severity",
    "status",
    "location",
    "problem",
    "fix",
)

# Markdown separator row matcher — at least one cell of three or more
# dashes (with optional surrounding whitespace and optional alignment
# colons). Used to skip the row between header and data.
_SEPARATOR_CELL = re.compile(r"^\s*:?-{3,}:?\s*$")


class Severity(StrEnum):
    """Documented severity vocabulary (BLOCK / HIGH / MEDIUM / LOW / INFO).

    Listed here so callers and tests have a single source of truth for
    the documented set, but **not** used as a type constraint on
    :class:`Finding`. The parser preserves whatever the reviewer wrote;
    vocabulary mapping is the caller's responsibility.
    """

    BLOCK = "BLOCK"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


@dataclass(frozen=True)
class Finding:
    """A single reviewer finding parsed from one table row.

    Attributes:
        id: The reviewer-supplied identifier (e.g. ``F1``).
        severity: Severity cell verbatim. Typed ``str`` (not
            :class:`Severity`) so unknown values reach the caller
            unchanged.
        status: Status cell verbatim. Typed ``str``; never coerced to a
            default by this parser.
        location: ``path:line`` or whatever the reviewer wrote.
        problem: Problem description verbatim, including any
            ``[constitution:A<n>]`` tag.
        fix: Suggested fix verbatim.
        source: Always ``external-<reviewer_id>``; the reviewer's value
            for this cell is dropped by design.
    """

    id: str
    severity: str
    status: str
    location: str
    problem: str
    fix: str
    source: str


class ParseWarning(UserWarning):
    """Emitted when a response contains no recognizable findings table.

    Also emitted when a candidate table is found but its header row is
    missing one or more required columns. Callers decide whether to
    surface the warning to the operator or treat it as a hard failure.
    """


def parse_response(
    response_text: str,
    reviewer_id: str,
    target: str,  # noqa: ARG001 - reserved for future per-target parsing branches
) -> tuple[Finding, ...]:
    """Extract findings from an external reviewer Markdown response.

    Locates the first Markdown table whose header row mentions every
    required column (``ID`` / ``Severity`` / ``Status`` / ``Location`` /
    ``Problem`` / ``Fix``), skips the separator row, and maps each data
    row to a :class:`Finding`. The ``source`` column on each finding is
    overwritten to ``external-<reviewer_id>``; ``severity`` and
    ``status`` cells flow through verbatim.

    Args:
        response_text: The raw Markdown body the reviewer returned.
        reviewer_id: Reviewer identifier used to populate the
            ``source`` field on every emitted finding.
        target: Either ``plan`` or ``code``. Reserved for future
            per-target parsing tweaks; currently unused at runtime so
            the dispatcher can pass through the active review target
            without per-call branching.

    Returns:
        Tuple of :class:`Finding` rows in source-document order. Empty
        tuple when no findings table is present (a ``ParseWarning`` is
        emitted in that case).

    Notes:
        Constitution tags (``[constitution:A<n>]``) embedded in the
        Problem column pass through verbatim — the dispatcher uses
        them to route findings back to the originating Article.
    """
    lines = response_text.splitlines()
    header_index, header_cells = _find_header(lines)
    if header_index is None or header_cells is None:
        warnings.warn(
            ParseWarning(f"no findings table located for reviewer {reviewer_id!r}"),
            stacklevel=2,
        )
        return ()

    column_index = _column_index_map(header_cells)

    data_start = _skip_separator_row(lines, header_index + 1)
    findings: list[Finding] = []
    for raw_line in lines[data_start:]:
        cells = _split_row(raw_line)
        if cells is None:
            # Blank line or non-table line ends the table block.
            break
        findings.append(_row_to_finding(cells, column_index, reviewer_id))
    return tuple(findings)


# --- internal helpers -----------------------------------------------------


def _split_row(line: str) -> list[str] | None:
    """Split a Markdown table row into cells, or return ``None``.

    Returns ``None`` for blank lines or lines that do not contain at
    least one ``|`` separator — both signal the end of the table block.
    Leading and trailing pipes are stripped so a row written as
    ``| a | b |`` and one written as ``a | b`` parse identically.
    """
    stripped = line.strip()
    if not stripped or "|" not in stripped:
        return None
    stripped = stripped.removeprefix("|").removesuffix("|")
    return [cell.strip() for cell in stripped.split("|")]


def _find_header(lines: list[str]) -> tuple[int | None, list[str] | None]:
    """Return ``(index, cells)`` of the first valid findings header row.

    A header row qualifies when its case-folded cells include every
    entry in :data:`_REQUIRED_COLUMNS`. The next non-blank line must
    look like a Markdown separator (``|---|---|...``) — without that
    guard, prose lines that happen to mention the column names would
    masquerade as headers.
    """
    for index, line in enumerate(lines):
        cells = _split_row(line)
        if cells is None:
            continue
        normalized = {cell.lower() for cell in cells if cell}
        if not all(col in normalized for col in _REQUIRED_COLUMNS):
            continue
        if not _next_line_is_separator(lines, index + 1):
            continue
        return index, cells
    return None, None


def _next_line_is_separator(lines: list[str], start: int) -> bool:
    """True when the next non-blank line is a Markdown separator row.

    Skips blank lines so an authoring style that spaces the header
    away from the separator still parses.
    """
    for line in lines[start:]:
        if not line.strip():
            continue
        cells = _split_row(line)
        if cells is None:
            return False
        return all(_SEPARATOR_CELL.match(cell) for cell in cells if cell)
    return False


def _skip_separator_row(lines: list[str], start: int) -> int:
    """Return the index of the first data row after the separator.

    Skips both leading blanks (header → blank → separator) and trailing
    blanks (separator → blank → first data row) so authoring whitespace
    around the separator does not truncate the parse.
    """
    index = start
    # Walk past blank lines to locate the separator itself.
    while index < len(lines) and not lines[index].strip():
        index += 1
    # Step over the separator row when present.
    if index < len(lines):
        cells = _split_row(lines[index])
        if cells is not None and all(_SEPARATOR_CELL.match(cell) for cell in cells if cell):
            index += 1
    # Walk past any blank lines between the separator and the first data row.
    while index < len(lines) and not lines[index].strip():
        index += 1
    return index


def _column_index_map(header_cells: list[str]) -> dict[str, int]:
    """Map each required column name to its index in ``header_cells``.

    Lower-cased lookup so reviewer capitalization (``Severity`` vs
    ``severity``) does not affect downstream cell extraction.
    """
    indices: dict[str, int] = {}
    for position, cell in enumerate(header_cells):
        key = cell.lower()
        if key in _REQUIRED_COLUMNS and key not in indices:
            indices[key] = position
    return indices


def _row_to_finding(
    cells: list[str],
    column_index: dict[str, int],
    reviewer_id: str,
) -> Finding:
    """Build a :class:`Finding` from a parsed data row.

    Cells short-of-header are treated as empty so a row that closes
    early ``| F1 | HIGH |`` still produces a row rather than crashing
    the parser.
    """

    def cell(name: str) -> str:
        idx = column_index[name]
        return cells[idx] if idx < len(cells) else ""

    return Finding(
        id=cell("id"),
        severity=cell("severity"),
        status=cell("status"),
        location=cell("location"),
        problem=cell("problem"),
        fix=cell("fix"),
        source=f"external-{reviewer_id}",
    )

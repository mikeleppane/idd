"""Pure ADD/REMOVE/MODIFY parser and applier for delta proposals.

This module parses the ``## Affects`` and ``## Delta`` sections of a
``proposal.md`` body, extracts an ordered list of ``DeltaOp`` records, and
applies those ops to a canonical SPEC.md body string.  No I/O, no subprocess,
no imports from ``tools.validate`` — entirely self-contained so the boundary
between the structural validator (``tools/validate/delta.py``) and the merger
is informative, not a shared dependency.

Locked op semantics (M3 spec §5.3.5, plan body 2026-05-08-m3-p5-change-deltas):

ADD
    Appends ``new_text`` verbatim (multi-line body preserved) to the bottom of
    the affected SPEC section, separated from existing content by exactly one
    blank line.

REMOVE
    Strips the line whose normalised text starts with ``anchor`` AND the
    indented/fenced continuation lines that immediately follow.  Ambiguous
    (N > 1 matches) or missing anchors are errors — never silent.

MODIFY
    Locates the anchor line + block and replaces the whole block with
    ``new_text``.  The ``old_text`` guard (``was "<old>"``) must be present in
    the matched line (after normalisation); mismatch is an error.

Block grammar (applied by both the parser and the applier)
---------------------------------------------------------
* An op marker (``+ ADD:``, ``- REMOVE:``, ``~ MODIFY:``) starts at column 0.
  Its first line is the *header line*.
* The op body extends from the header line through:
  - all subsequent indented lines (any leading whitespace), AND
  - all subsequent fenced blocks (between matching ``` fences) regardless of
    indentation,
  - until the next op marker at column 0, an H2 (``## ``), or EOF.
* Blank lines (column-0, all whitespace) always join the current op body.
  If the next non-blank line starts a new op marker or H2, those trailing
  blank lines become trailing whitespace of the prior op (stripped by
  callers where relevant).
* An op marker inside an open fenced block is treated as literal text — it
  does NOT open a new op.

Section resolution
------------------
* ``## Affects`` is parsed for ``sections [<list>]``.  If exactly one section
  is listed, every op without a ``[Section]`` tag binds to it.
* If two-or-more sections are listed, every op MUST carry a ``[Section]`` tag
  (e.g. ``+ ADD: [Scenarios] ...``).  Untagged op when >= 2 sections →
  ``DeltaMergeError``.
* A tag whose section is not in the ``## Affects`` list → ``DeltaMergeError``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Module-level regex constants
# ---------------------------------------------------------------------------

# Matches the op marker prefix at column 0.
_OP_HEADER_RE = re.compile(r"^([+\-~]) (ADD|REMOVE|MODIFY): ?(.*)", re.MULTILINE)

# Matches the ## Affects section header (exact, trailing whitespace allowed).
_AFFECTS_RE = re.compile(r"^## Affects\s*$", re.MULTILINE)

# Matches the ## Delta section header (exact, trailing whitespace allowed).
_DELTA_HEADER_RE = re.compile(r"^## Delta\s*$", re.MULTILINE)

# Matches any H2 header line.
_H2_RE = re.compile(r"^## ", re.MULTILINE)

# Matches the opening/closing of a fenced code block (``` possibly with language tag).
_FENCE_RE = re.compile(r"^\s*```")

# Matches an indented line (any leading whitespace).
_INDENTED_RE = re.compile(r"^\s+")

# Extracts was/now from MODIFY header: was "old", now "new"
# The anchor is everything before " — was " (or " was " without em-dash).
# old-text capture uses [^"]+ (non-empty) — an empty was "" guard is not
# meaningful and is rejected at parse time.
_MODIFY_FORM_RE = re.compile(
    r'^(?P<anchor>.*?)\s*(?:—|-{1,2})\s*was\s+"(?P<old>[^"]+)"\s*,\s*now\s+"(?P<new>[^"]*)"',
)

# Extracts an optional [Section] prefix from an op's rest-of-header text.
_SECTION_TAG_RE = re.compile(r"^\[(?P<section>[^\]]+)\]\s*(?P<rest>.*)$")

# Matches a sections list in ## Affects body: sections [A, B, C]
_SECTIONS_LIST_RE = re.compile(r"sections\s+\[(?P<list>[^\]]+)\]")

# Number of declared sections at/above which every op MUST carry a [Section] tag (§5.3.5).
_MULTI_SECTION_THRESHOLD: int = 2


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class DeltaMergeError(RuntimeError):
    """Raised when a delta proposal cannot be parsed or applied."""


@dataclass(frozen=True)
class DeltaOp:
    """A single parsed delta operation.

    Attributes:
        kind: One of ``"ADD"``, ``"REMOVE"``, ``"MODIFY"``.
        section: The SPEC.md H2 section the op targets (resolved from
            ``## Affects`` + optional ``[Section]`` tag).
        anchor: The criterion id / line locator named in the proposal
            (e.g. ``"criterion 2"``).
        old_text: MODIFY only — the ``was "<old>"`` guard payload.
            ``None`` for ADD and REMOVE.
        new_text: Multi-line body for ADD/MODIFY; empty string for REMOVE.
    """

    kind: Literal["ADD", "REMOVE", "MODIFY"]
    section: str
    anchor: str
    old_text: str | None
    new_text: str


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _normalize_anchor(s: str) -> str:
    """Lowercase, strip, and collapse internal whitespace in ``s``.

    Used for both anchor keys and candidate canonical lines when matching
    REMOVE / MODIFY targets.

    Args:
        s: Raw string to normalise.

    Returns:
        Normalised string.
    """
    return " ".join(s.lower().split())


def _extract_body_section(text: str, header_re: re.Pattern[str]) -> str | None:
    """Return text from the line after *header_re* match to the next H2 (or EOF).

    Fence-aware: an ``## Inside fence`` line that lives inside a fenced code
    block does NOT terminate the section.  Without this guard, a delta op
    body containing illustrative ``## ...`` lines would truncate the
    ``## Delta`` section and corrupt the parsed op body.

    Args:
        text: Full document text.
        header_re: Compiled pattern that matches the H2 header line.

    Returns:
        Section body string, or ``None`` if the header is absent.
    """
    m = header_re.search(text)
    if m is None:
        return None
    section_start = m.end()

    inside_fence = False
    pos = section_start
    while pos < len(text):
        nl = text.find("\n", pos)
        line_end = len(text) if nl == -1 else nl + 1
        line = text[pos:line_end]
        if _FENCE_RE.match(line):
            inside_fence = not inside_fence
        elif not inside_fence and line.startswith("## "):
            return text[section_start:pos]
        pos = line_end
    return text[section_start:]


def _parse_sections_list(affects_body: str) -> list[str]:
    """Parse the ``sections [A, B, C]`` list from the Affects section body.

    Args:
        affects_body: Text of the ``## Affects`` section (header excluded).

    Returns:
        List of section name strings, stripped of whitespace.
    """
    m = _SECTIONS_LIST_RE.search(affects_body)
    if m is None:
        return []
    raw = m.group("list")
    return [s.strip() for s in raw.split(",") if s.strip()]


def _collect_op_body_lines(lines: list[str], start: int) -> tuple[list[str], int]:
    """Collect lines belonging to the current op body, starting at *start*.

    The body ends at:
    * an op marker at column 0 (when not inside a fence), OR
    * an H2 header line at column 0 (when not inside a fence), OR
    * EOF.

    Blank lines (all whitespace) always join the current op body.

    Args:
        lines: All lines of the ``## Delta`` section.
        start: Index of the first line *after* the header line.

    Returns:
        A tuple of (body_lines, next_index) where body_lines are the lines
        belonging to the op body and next_index is where the next op starts.
    """
    body: list[str] = []
    inside_fence = False
    i = start
    while i < len(lines):
        line = lines[i]
        # Toggle fence state
        if _FENCE_RE.match(line):
            inside_fence = not inside_fence
            body.append(line)
            i += 1
            continue
        if inside_fence:
            # Inside a fence: everything is literal content
            body.append(line)
            i += 1
            continue
        # Outside a fence: check for terminators
        stripped = line.rstrip("\n")
        if not stripped.strip():
            # Blank line — tentatively add (may be trailing)
            body.append(line)
            i += 1
            continue
        # Non-blank, non-fence line at column 0
        if not _INDENTED_RE.match(line) and (_OP_HEADER_RE.match(line) or _H2_RE.match(line)):
            break
        body.append(line)
        i += 1
    return body, i


def _resolve_section_and_rest(
    rest: str,
    declared_sections: list[str],
    single_section: str | None,
    multi_section: bool,
) -> tuple[str, str]:
    """Resolve the target section and strip any ``[Section]`` tag from *rest*.

    Args:
        rest: Everything after the op kind token on the header line.
        declared_sections: Section names from ``## Affects``.
        single_section: The sole declared section name when exactly one was
            listed, else ``None``.
        multi_section: ``True`` when two or more sections are declared.

    Returns:
        ``(section, rest_without_tag)`` tuple.

    Raises:
        DeltaMergeError: On missing required tag or undeclared tag.
    """
    section_m = _SECTION_TAG_RE.match(rest)
    if section_m:
        section = section_m.group("section").strip()
        rest = section_m.group("rest").strip()
        if section not in declared_sections:
            raise DeltaMergeError(f"op section {section!r} not declared in ## Affects")
        return section, rest
    if multi_section:
        raise DeltaMergeError(
            "op missing required [Section] tag; ## Affects lists multiple sections"
        )
    return single_section or "", rest


def _parse_op_fields(
    kind: Literal["ADD", "REMOVE", "MODIFY"],
    rest: str,
    body_text: str,
) -> tuple[str, str | None, str]:
    """Extract ``(anchor, old_text, new_text)`` from the op header and body.

    Args:
        kind: Parsed op kind.
        rest: Header line text after any ``[Section]`` tag.
        body_text: Collected multi-line body, trailing whitespace stripped.

    Returns:
        ``(raw_anchor, old_text, new_text)`` triple.

    Raises:
        DeltaMergeError: When a MODIFY header lacks the required form.
    """
    if kind == "MODIFY":
        fm = _MODIFY_FORM_RE.match(rest)
        if fm is None:
            raise DeltaMergeError(f'MODIFY missing \'was "<old>", now "<new>"\' form on: {rest!r}')
        raw_anchor = fm.group("anchor").strip()
        old_text: str | None = fm.group("old")
        new_text = body_text or fm.group("new")
        return raw_anchor, old_text, new_text
    if kind == "REMOVE":
        return rest, None, ""
    # ADD
    return rest, None, body_text


def _build_ops_from_delta(
    delta_body: str,
    declared_sections: list[str],
) -> list[DeltaOp]:
    """Walk delta body line-by-line, building DeltaOp records.

    Args:
        delta_body: Text of the ``## Delta`` section (header excluded).
        declared_sections: Section names from ``## Affects``.

    Returns:
        Ordered list of parsed ``DeltaOp`` records.

    Raises:
        DeltaMergeError: On structural violations.
    """
    single_section = declared_sections[0] if len(declared_sections) == 1 else None
    multi_section = len(declared_sections) >= _MULTI_SECTION_THRESHOLD

    lines = delta_body.splitlines(keepends=True)
    ops: list[DeltaOp] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        m = _OP_HEADER_RE.match(line)
        if m is None:
            i += 1
            continue

        kind_str = m.group(2)
        rest = m.group(3).strip()

        # Determine kind
        kind: Literal["ADD", "REMOVE", "MODIFY"]
        if kind_str == "ADD":
            kind = "ADD"
        elif kind_str == "REMOVE":
            kind = "REMOVE"
        else:
            kind = "MODIFY"

        # Resolve [Section] tag
        section, rest = _resolve_section_and_rest(
            rest, declared_sections, single_section, multi_section
        )

        # Collect multi-line body
        body_lines, i = _collect_op_body_lines(lines, i + 1)
        body_text = "".join(body_lines).rstrip()

        # Parse anchor / old_text / new_text depending on kind
        raw_anchor, old_text, new_text = _parse_op_fields(kind, rest, body_text)

        ops.append(
            DeltaOp(
                kind=kind,
                section=section,
                anchor=raw_anchor,
                old_text=old_text,
                new_text=new_text,
            )
        )

    return ops


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_proposal_body(text: str) -> list[DeltaOp]:
    r"""Parse a proposal document and return an ordered list of ``DeltaOp``\s.

    The text may contain YAML frontmatter (delimited by ``---``).  Only the
    ``## Affects`` and ``## Delta`` sections are interpreted; all other sections
    are ignored.

    Args:
        text: Full text of the proposal document (including optional
            frontmatter).

    Returns:
        Ordered list of ``DeltaOp`` records.

    Raises:
        DeltaMergeError: When:
            * ``## Affects`` section is absent.
            * ``## Delta`` section is absent or contains no op markers.
            * An op is untagged when ``## Affects`` lists multiple sections.
            * An op carries a ``[Section]`` tag not declared in ``## Affects``.
            * A MODIFY op lacks the ``was "<old>", now "<new>"`` form.
    """
    # Extract ## Affects section
    affects_body = _extract_body_section(text, _AFFECTS_RE)
    if affects_body is None:
        raise DeltaMergeError("missing ## Affects section")

    declared_sections = _parse_sections_list(affects_body)

    # Extract ## Delta section
    delta_body = _extract_body_section(text, _DELTA_HEADER_RE)
    if delta_body is None:
        raise DeltaMergeError("missing ## Delta section")

    # Verify the delta section contains at least one op marker
    if not _OP_HEADER_RE.search(delta_body):
        raise DeltaMergeError("missing ## Delta section or no op markers present")

    return _build_ops_from_delta(delta_body, declared_sections)


def _mask_fenced_lines(lines: list[str]) -> list[str]:
    r"""Return a copy of *lines* with fenced-block contents replaced by blank lines.

    Lines that open or close a fence (````` ``` `````) are themselves blanked out.
    This preserves list length and all line indices so callers can safely match
    against the masked copy while using the original indices for slicing.

    Args:
        lines: All lines of the document (or a section thereof).

    Returns:
        A new list of the same length; lines inside (and including) fence
        markers are replaced with ``"\n"``; lines outside fences are unchanged.
    """
    masked: list[str] = []
    inside_fence = False
    for line in lines:
        if _FENCE_RE.match(line):
            inside_fence = not inside_fence
            masked.append("\n")  # blank out the fence marker line itself
        elif inside_fence:
            masked.append("\n")  # blank out content inside the fence
        else:
            masked.append(line)
    return masked


def _extract_section_bounds(lines: list[str], section_name: str) -> tuple[int, int]:
    """Find the start and end line indices of a named H2 section.

    Fence-aware: H2 headers that appear inside a fenced code block are
    ignored so that illustrative ``## <Name>`` examples in the canonical body
    cannot shadow the real section heading.

    Args:
        lines: All lines of the canonical body.
        section_name: The H2 heading name to locate (without ``## ``).

    Returns:
        ``(content_start, content_end)`` line indices (exclusive of the
        heading line itself; *content_end* points to first line of the next H2
        or len(lines)).

    Raises:
        DeltaMergeError: When *section_name* is not found.
    """
    heading = f"## {section_name}"
    masked = _mask_fenced_lines(lines)
    for idx, line in enumerate(masked):
        if line.rstrip() == heading:
            content_start = idx + 1
            for j in range(content_start, len(masked)):
                if masked[j].startswith("## ") and masked[j].rstrip() != heading:
                    return content_start, j
            return content_start, len(lines)
    raise DeltaMergeError(f"section not found in canonical body: {section_name!r}")


def _extract_anchor_block(
    lines: list[str],
) -> list[tuple[int, int]]:
    """Return ``(start, end)`` pairs for every top-level (column-0, non-H2) block.

    Each block starts at a non-blank, column-0 line and extends through all
    immediately following indented/fenced continuation lines.

    Args:
        lines: Lines of a section body (heading excluded).

    Returns:
        List of ``(start_idx, end_idx)`` pairs (end exclusive).
    """
    blocks: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.rstrip("\n")
        if not stripped.strip():
            i += 1
            continue
        if _INDENTED_RE.match(line):
            i += 1
            continue
        # Non-blank, column-0 line — start of a block
        block_start = i
        i += 1
        inside_fence = False
        while i < len(lines):
            bl = lines[i]
            if _FENCE_RE.match(bl):
                inside_fence = not inside_fence
                i += 1
                continue
            if inside_fence:
                i += 1
                continue
            bstripped = bl.rstrip("\n")
            if not bstripped.strip():
                # Blank — peek ahead
                j = i + 1
                while j < len(lines) and not lines[j].rstrip("\n").strip():
                    j += 1
                if j < len(lines) and _INDENTED_RE.match(lines[j]):
                    # Continuation after blank
                    i += 1
                    continue
                # Blank at end of block
                break
            if _INDENTED_RE.match(bl):
                i += 1
                continue
            # Another column-0 non-blank line — end of block
            break
        blocks.append((block_start, i))
    return blocks


def _find_anchor_blocks(section_lines: list[str], anchor: str) -> list[tuple[int, int]]:
    """Find all blocks in *section_lines* whose first line starts with *anchor* (normalised).

    Args:
        section_lines: Lines within the target section (heading excluded).
        anchor: Normalised anchor string.

    Returns:
        List of ``(start, end)`` pairs.
    """
    blocks = _extract_anchor_block(section_lines)
    matches = []
    for start, end in blocks:
        first_line = section_lines[start].rstrip("\n")
        if _normalize_anchor(first_line).startswith(anchor):
            matches.append((start, end))
    return matches


def apply_delta_ops(canonical_body: str, ops: list[DeltaOp]) -> str:
    """Apply *ops* to *canonical_body*, returning the modified string.

    Ops are applied in order, each op working against the running result of the
    previous op (top-to-bottom against the running merged body).

    Args:
        canonical_body: Full text of the canonical SPEC.md file.
        ops: Ordered list of ``DeltaOp`` records from ``parse_proposal_body``.

    Returns:
        Modified canonical body string.

    Raises:
        DeltaMergeError: For any anchor-not-found, ambiguous-anchor, guard
            mismatch, or section-not-found error.
    """
    body = canonical_body
    for op in ops:
        body = _apply_single_op(body, op)
    return body


def _apply_single_op(body: str, op: DeltaOp) -> str:
    """Apply a single DeltaOp to *body* and return the result.

    Args:
        body: Current canonical body string.
        op: The operation to apply.

    Returns:
        Updated body string.

    Raises:
        DeltaMergeError: On structural failures.
    """
    lines = body.splitlines(keepends=True)
    content_start, content_end = _extract_section_bounds(lines, op.section)
    section_lines = lines[content_start:content_end]

    if op.kind == "ADD":
        return _apply_add(lines, content_end, op)
    if op.kind == "REMOVE":
        return _apply_remove(lines, section_lines, content_start, op)
    # MODIFY
    return _apply_modify(lines, section_lines, content_start, op)


def _apply_add(
    lines: list[str],
    content_end: int,
    op: DeltaOp,
) -> str:
    """Insert ADD new_text before the section end marker.

    Args:
        lines: All lines of the document.
        content_end: Index where the section ends (first line of next H2 or EOF).
        op: The ADD op.

    Returns:
        Updated body string.
    """
    # Find the last non-blank line in the section to insert after it
    insert_after = content_end
    for j in range(content_end - 1, -1, -1):
        if lines[j].strip():
            insert_after = j + 1
            break

    new_lines = ["\n"] + [
        ln + "\n" if not ln.endswith("\n") else ln for ln in op.new_text.splitlines()
    ]
    updated = lines[:insert_after] + new_lines + lines[insert_after:]
    return "".join(updated)


def _apply_remove(
    lines: list[str],
    section_lines: list[str],
    content_start: int,
    op: DeltaOp,
) -> str:
    """Remove the anchor block from the section.

    Args:
        lines: All document lines.
        section_lines: Lines within the target section.
        content_start: Offset of section_lines in lines.
        op: The REMOVE op.

    Returns:
        Updated body string.

    Raises:
        DeltaMergeError: On missing or ambiguous anchor.
    """
    norm_anchor = _normalize_anchor(op.anchor)
    matches = _find_anchor_blocks(section_lines, norm_anchor)

    if not matches:
        raise DeltaMergeError(f"REMOVE anchor not found: {op.anchor!r}")
    if len(matches) > 1:
        raise DeltaMergeError(f"REMOVE anchor ambiguous: anchor matched {len(matches)} lines")

    start, end = matches[0]
    abs_start = content_start + start
    abs_end = content_start + end

    # Also remove a trailing blank line if present right after the block
    extra = 0
    if abs_end < len(lines) and not lines[abs_end].strip():
        extra = 1

    updated = lines[:abs_start] + lines[abs_end + extra :]
    return "".join(updated)


def _apply_modify(
    lines: list[str],
    section_lines: list[str],
    content_start: int,
    op: DeltaOp,
) -> str:
    """Replace the anchor block with op.new_text.

    Args:
        lines: All document lines.
        section_lines: Lines within the target section.
        content_start: Offset of section_lines in lines.
        op: The MODIFY op.

    Returns:
        Updated body string.

    Raises:
        DeltaMergeError: On missing/ambiguous anchor or guard mismatch.
    """
    norm_anchor = _normalize_anchor(op.anchor)
    matches = _find_anchor_blocks(section_lines, norm_anchor)

    if not matches:
        raise DeltaMergeError(f"MODIFY anchor not found: {op.anchor!r}")
    if len(matches) > 1:
        raise DeltaMergeError(
            f"MODIFY anchor ambiguous: anchor matched {len(matches)} lines for {op.anchor!r}"
        )

    start, end = matches[0]
    abs_start = content_start + start
    abs_end = content_start + end

    # Guard check
    if op.old_text is not None:
        if not op.old_text.strip():
            raise DeltaMergeError(f"MODIFY 'was' guard cannot be empty for anchor {op.anchor!r}")
        matched_first_line = section_lines[start].rstrip("\n")
        norm_line = _normalize_anchor(matched_first_line)
        norm_guard = _normalize_anchor(op.old_text)
        if norm_guard not in norm_line:
            raise DeltaMergeError(
                f"MODIFY guard mismatch: anchor {op.anchor!r} matched but "
                f"{op.old_text!r} not present"
            )

    # Build replacement lines
    replacement = [ln + "\n" if not ln.endswith("\n") else ln for ln in op.new_text.splitlines()]
    if replacement and not replacement[-1].endswith("\n"):
        replacement[-1] += "\n"

    updated = lines[:abs_start] + replacement + lines[abs_end:]
    return "".join(updated)

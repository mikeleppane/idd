"""Manual-mode orchestration helpers for the cross-AI substrate.

The skill (``forge-review``) drives every cross-AI manual cycle from the
conversation; this module is the mechanical layer the skill calls into.
Each helper performs a single, self-contained filesystem or formatting
step so the skill never has to inline path arithmetic or table-row
escaping.

Public surface (in ``__all__``)
-------------------------------

* :func:`write_prompt_to_disk` — atomically write a reviewer-bound
  Markdown ``body`` (the post-redaction text the operator will pipe to
  the external CLI) to
  ``.forge/features/<id>/cross-ai/<target>-<utc>-prompt.md`` and return
  the absolute path. ``now=`` is injectable so tests can lock the
  timestamp portion of the filename. The helper takes the body as a
  plain string, **not** a :class:`tools.cross_ai.prompt.Prompt`, so the
  caller cannot accidentally persist the unredacted ``Prompt.body``
  while pretending to ship the post-scrub bytes.
* :func:`read_paste_response` — UTF-8 read of a pasted reviewer
  response. Errors propagate (``FileNotFoundError`` / ``UnicodeDecodeError``)
  so the skill can surface a precise failure rather than a silent miss.
* :func:`extract_reviewer_id` — pull the ``reviewer:`` field out of the
  optional YAML frontmatter that wraps a pasted response. Returns
  ``None`` when frontmatter is absent or the field is missing so the
  caller can fall back to a ``--reviewer <name>`` flag or ``"unknown"``.
* :func:`merge_findings_into_review` — append parsed
  :class:`tools.cross_ai.parse.Finding` rows to the existing
  ``REVIEW.<target>.md`` table. Empty input is a fast-path no-op; a
  missing review file is fatal so the skill knows it forgot the
  template-copy step.
* :func:`format_disclosure_summary` — render the pre-dispatch
  :class:`tools.cross_ai.disclosure.Disclosure` snapshot as the
  multi-line plain-text block the dispatcher prints to the operator.
  The text is a stable contract (snapshot-tested) — every visible
  newline below is part of the agreed format. Path placeholders inside
  the rendered shell command are wrapped with :func:`shlex.quote` so a
  repo path containing whitespace still produces a runnable command.

Caller responsibilities
-----------------------

This module never touches subprocess, never spawns CLIs, never loads
config, and never validates Finding schemas. Detection / cost /
disclosure / config concerns live in the sibling modules; the skill
orchestrates them and hands the typed results to the helpers below.

Atomicity
---------

``write_prompt_to_disk`` and ``merge_findings_into_review`` write via
``tempfile.mkstemp`` + ``Path.replace`` (mirrors :mod:`tools.state`'s
state-file pattern). The intermediate file lives in the same directory
as the destination so the rename is a same-filesystem move and atomic on
POSIX; a partial write is cleaned up on any exception so callers never
observe a torn file.
"""

from __future__ import annotations

import contextlib
import os
import shlex
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from tools.cross_ai.disclosure import Disclosure
from tools.cross_ai.parse import Finding
from tools.cross_ai.prompt import PromptTarget

__all__ = (
    "extract_reviewer_id",
    "format_disclosure_summary",
    "merge_findings_into_review",
    "read_paste_response",
    "write_prompt_to_disk",
)


# Filesystem timestamp shape — colons in the canonical ISO-8601 string
# are replaced with hyphens so the resulting filename is portable across
# filesystems that reject ``:`` (notably Windows / SMB shares).
_TIMESTAMP_FMT: str = "%Y-%m-%dT%H-%M-%SZ"

# Default paste-back hint surfaced inside the disclosure summary when the
# caller does not override ``paste_back_command``. Kept as a module
# constant rather than inlined so a future rename of the skill command
# is a one-line change.
_DEFAULT_PASTE_BACK_COMMAND: str = "/forge:review --cross-ai-paste response.md"

# Frontmatter fence literal. A pasted reviewer response MAY open with
# ``---`` on the very first line followed by ``key: value`` lines and a
# closing ``---`` fence. Anything else (including a CRLF-prefixed
# variant) falls through to the no-frontmatter branch.
_FRONTMATTER_FENCE: str = "---"


def _atomic_write_text(path: Path, body: str, *, prefix: str) -> None:
    """Write ``body`` to ``path`` via ``mkstemp`` + ``Path.replace``.

    The temp file is created in ``path.parent`` so the rename stays on
    the same filesystem and ``os.replace`` is atomic (POSIX). On any
    failure mid-write the partial temp file is removed so callers never
    observe a torn destination file. ``prefix`` is purely for debug
    visibility — it lets ``ls`` distinguish prompt temps from review
    temps if a write happens to crash mid-flight.
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=str(parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        tmp_path.replace(path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
        raise


def write_prompt_to_disk(
    body: str,
    target: PromptTarget,
    feature_id: str,
    repo_root: Path,
    *,
    now: datetime | None = None,
) -> Path:
    """Persist a reviewer-bound prompt ``body`` to the cross-AI directory.

    The destination is
    ``<repo_root>/.forge/features/<feature_id>/cross-ai/<target>-<utc>-prompt.md``.
    The parent directory is created if absent so the skill never has to
    pre-seed it.

    The helper takes ``body`` as a plain string rather than a
    :class:`tools.cross_ai.prompt.Prompt` so the caller is forced to
    decide which bytes ship to the external CLI. In production the
    caller passes :attr:`tools.redaction.RedactionResult.output_text`
    (the post-scrub body); passing the raw ``Prompt.body`` would defeat
    the redaction step that ran moments earlier. The string-level API
    makes the redaction-vs-raw choice explicit at every call site.

    Args:
        body: Markdown body to persist verbatim. In manual mode this is
            the post-redaction text the operator will pipe to the
            reviewer CLI; the helper does NOT redact.
        target: ``PromptTarget.plan`` or ``PromptTarget.code`` — drives
            the filename prefix.
        feature_id: Folder name under ``.forge/features/``.
        repo_root: Repository root containing ``.forge/``.
        now: Injectable UTC clock; defaults to ``datetime.now(UTC)``.
            Tests pass a fixed value so the filename timestamp is
            deterministic.

    Returns:
        Absolute path to the written file. Callers persist this in
        state.json so the paste-back step can locate the prompt without
        recomputing the timestamp.
    """
    timestamp = (now or datetime.now(UTC)).strftime(_TIMESTAMP_FMT)
    target_dir = repo_root / ".forge" / "features" / feature_id / "cross-ai"
    prompt_path = target_dir / f"{target.value}-{timestamp}-prompt.md"
    _atomic_write_text(prompt_path, body, prefix=".cross-ai-prompt-")
    return prompt_path.resolve()


def read_paste_response(path: Path) -> str:
    """Read a pasted reviewer response from disk.

    A thin UTF-8 read — no normalization, no trimming, no fallback
    encoding. ``FileNotFoundError`` and ``UnicodeDecodeError`` propagate
    unchanged so the skill can surface a precise failure to the operator
    instead of silently treating a missing or mojibake file as an empty
    response.
    """
    return path.read_text(encoding="utf-8")


def _find_frontmatter_block(lines: list[str]) -> list[str] | None:
    """Return the body lines of the leading YAML frontmatter, or ``None``.

    The helper accepts a frontmatter only when the very first line is a
    bare ``---`` and a matching closing ``---`` appears later in the
    document. Anything else (no fence on line 1, no closing fence)
    yields ``None`` so the caller treats the input as un-frontmattered.
    """
    if not lines or lines[0].strip() != _FRONTMATTER_FENCE:
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() == _FRONTMATTER_FENCE:
            return lines[1:index]
    return None


def _unquote_value(value: str) -> str | None:
    """Strip surrounding quotes from a YAML scalar, returning ``None`` if empty.

    Wraps :func:`shlex.split` to peel ``"codex"`` / ``'codex'`` style
    wrappers; un-quoted values pass through unchanged. A malformed
    quote (raises ``ValueError`` from ``shlex``) falls back to the raw
    value rather than dropping the field.
    """
    try:
        tokens = shlex.split(value, posix=True)
    except ValueError:
        return value or None
    if not tokens:
        return None
    return tokens[0]


def extract_reviewer_id(response_text: str) -> str | None:
    """Return the ``reviewer:`` value from optional YAML frontmatter.

    A pasted reviewer response MAY open with a YAML frontmatter block::

        ---
        reviewer: codex
        timestamp: 2026-05-10T12:00:00Z
        ---

        # Findings ...

    The helper scans for that exact opening fence on line 1 and a
    closing fence on a later line; everything between is parsed as
    ``key: value`` lines. The ``reviewer`` value (stripped) is
    returned; ``None`` is returned when:

    * the response does not open with ``---`` on its first line, or
    * no closing ``---`` fence is found, or
    * the frontmatter parses but contains no ``reviewer`` field, or
    * the ``reviewer`` field is present but empty after stripping.

    Returning ``None`` lets the skill fall back to a ``--reviewer
    <name>`` flag or the ``"unknown"`` sentinel without inventing a
    hidden default. Quoted values (single or double) are unquoted via
    :func:`shlex.split`; values with embedded ``:`` characters survive
    because we only split on the FIRST ``:``.
    """
    if not response_text:
        return None
    block = _find_frontmatter_block(response_text.splitlines())
    if block is None:
        return None
    for raw in block:
        key, separator, raw_value = raw.partition(":")
        if not separator or key.strip().lower() != "reviewer":
            continue
        value = raw_value.strip()
        return _unquote_value(value) if value else None
    return None


def _count_trailing_backslashes(value: str, end: int) -> int:
    """Count backslashes immediately preceding index ``end`` in ``value``.

    Used by :func:`_escape_pipes` to decide whether a ``|`` is "already
    escaped". A pipe is already escaped when the count of immediately
    preceding backslashes is odd (each pair of backslashes is itself an
    escaped backslash, so an odd remainder is the live escape).
    """
    count = 0
    cursor = end - 1
    while cursor >= 0 and value[cursor] == "\\":
        count += 1
        cursor -= 1
    return count


def _escape_pipes(value: str) -> str:
    r"""Escape literal ``|`` characters so a Markdown table row stays well-formed.

    A pipe inside a cell would split it; the standard escape is ``\|``.
    A pipe is treated as already-escaped only when an *odd* number of
    backslashes immediately precedes it — an even count means each
    backslash is itself escaped (``\\``) and the trailing pipe is live.
    This keeps a literal ``\\|`` (escaped backslash followed by literal
    pipe) round-trippable instead of swallowing the backslash.
    """
    out: list[str] = []
    for index, char in enumerate(value):
        if char != "|":
            out.append(char)
            continue
        if _count_trailing_backslashes(value, index) % 2 == 1:
            # Already escaped — preserve verbatim.
            out.append("|")
            continue
        out.append("\\|")
    return "".join(out)


def _format_finding_row(finding: Finding) -> str:
    """Render a single :class:`Finding` as a pipe-separated table row."""
    return (
        f"| {_escape_pipes(finding.id)} "
        f"| {_escape_pipes(finding.severity)} "
        f"| {_escape_pipes(finding.status)} "
        f"| {_escape_pipes(finding.location)} "
        f"| {_escape_pipes(finding.problem)} "
        f"| {_escape_pipes(finding.fix)} "
        f"| {_escape_pipes(finding.source)} |"
    )


# A Markdown table row needs at least one opening and one closing pipe
# (``|cell|``) — the minimum well-formed shape. We compare against this
# constant rather than inlining the literal so the reasoning is explicit.
_MIN_TABLE_ROW_PIPES: int = 2


def _is_table_row(line: str) -> bool:
    """True when ``line`` looks like a Markdown table row.

    A table row begins with a pipe (after stripping leading whitespace)
    and contains at least one additional pipe — that second pipe is what
    closes the first cell. Anything else (blank lines, prose, headings)
    breaks the table block.
    """
    stripped = line.lstrip()
    return stripped.startswith("|") and stripped.count("|") >= _MIN_TABLE_ROW_PIPES


def _is_findings_heading(line: str) -> bool:
    """Tolerant match for the ``# Findings`` heading.

    Accepts any heading depth (``#``, ``##``, ``###`` …) and any
    trailing suffix (``# Findings``, ``# Findings (cycle 2)``,
    ``## Findings — external``) so future template variants do not
    silently fall through. The required shape is one or more leading
    ``#`` characters, one space, then a token whose lowercase form
    equals ``findings``.
    """
    stripped = line.strip()
    if not stripped.startswith("#"):
        return False
    hashes, _, remainder = stripped.partition(" ")
    if set(hashes) != {"#"}:
        return False
    first_word = remainder.strip().split(" ", maxsplit=1)[0].lower()
    return first_word == "findings"


def _insert_index_after_findings_table(lines: list[str]) -> int:
    """Locate the insertion point immediately after the Findings table block.

    Returns the index at which a new row should be inserted so it lands
    after the last existing data row (or after the separator row when
    the table has no data rows yet). Raises ``ValueError`` when no
    ``# Findings`` heading is present so the caller can surface the
    missing-table condition rather than silently appending at EOF.
    """
    heading_index: int | None = None
    for index, line in enumerate(lines):
        if _is_findings_heading(line):
            heading_index = index
            break
    if heading_index is None:
        raise ValueError("REVIEW file is missing a '# Findings' heading")

    # Walk forward to the first table row after the heading.
    cursor = heading_index + 1
    while cursor < len(lines) and not _is_table_row(lines[cursor]):
        cursor += 1
    if cursor >= len(lines):
        raise ValueError("REVIEW file '# Findings' section has no table")

    # ``cursor`` now points at the header row. The next table row is the
    # separator; everything after it (until the block breaks) is data.
    last_table_row = cursor
    cursor += 1
    while cursor < len(lines) and _is_table_row(lines[cursor]):
        last_table_row = cursor
        cursor += 1
    return last_table_row + 1


def merge_findings_into_review(
    findings: tuple[Finding, ...],
    target: PromptTarget,
    feature_id: str,
    repo_root: Path,
) -> int:
    """Append ``findings`` to ``REVIEW.<target>.md`` and return the row count.

    The findings rows are inserted directly after the last existing row
    of the ``# Findings`` table — frontmatter, the heading, the column
    header, the separator, and any pre-existing data rows are preserved
    verbatim. Empty ``findings`` is a fast-path no-op (returns ``0``
    without touching the file). A missing REVIEW file is fatal: the
    skill is expected to have copied the template before requesting a
    merge, so its absence is a routing bug, not a recoverable state.

    Args:
        findings: Parsed reviewer rows. Empty tuple → no-op.
        target: ``plan`` or ``code`` — selects the ``REVIEW.<target>.md``
            destination.
        feature_id: Folder name under ``.forge/features/``.
        repo_root: Repository root containing ``.forge/``.

    Returns:
        The number of rows appended (always equals ``len(findings)``;
        the helper never deduplicates).

    Raises:
        FileNotFoundError: ``REVIEW.<target>.md`` does not exist; the
            offending path is included in the message.
        ValueError: The file exists but is missing a ``# Findings``
            heading or has no table beneath it. Indicates a corrupted
            template, not a routing bug.
    """
    if not findings:
        return 0

    review_path = repo_root / ".forge" / "features" / feature_id / f"REVIEW.{target.value}.md"
    if not review_path.exists():
        raise FileNotFoundError(
            f"REVIEW file not found for feature {feature_id!r} target {target.value!r} "
            f"at {review_path}"
        )

    lines = review_path.read_text(encoding="utf-8").splitlines(keepends=True)
    # ``splitlines(keepends=True)`` preserves each line's trailing newline
    # so reassembly is lossless. We need plain lines for the heading scan
    # though — strip ends locally rather than re-reading the file.
    plain_lines = [line.rstrip("\n") for line in lines]
    insert_at = _insert_index_after_findings_table(plain_lines)

    new_rows = [_format_finding_row(finding) + "\n" for finding in findings]
    merged = lines[:insert_at] + new_rows + lines[insert_at:]

    _atomic_write_text(review_path, "".join(merged), prefix=".cross-ai-review-")
    return len(findings)


def _yes_no(flag: bool) -> str:
    """Render a bool as the ``yes``/``no`` literal the snapshot expects."""
    return "yes" if flag else "no"


def format_disclosure_summary(
    disclosure: Disclosure,
    prompt_path: Path,
    *,
    paste_back_command: str | None = None,
) -> str:
    """Render the pre-dispatch disclosure as the operator-facing snapshot.

    The text is a stable contract — the dispatcher prints it verbatim
    before any external CLI runs, and a snapshot test locks the format.
    Two-space indents, lowercase ``yes``/``no`` flag literals, and the
    blank lines between the metadata block and the run/paste hints are
    all load-bearing.

    Path placeholders inside the rendered shell command are wrapped with
    :func:`shlex.quote` so a repo path containing whitespace (or any
    other shell-meaningful character) still produces a copy-pasteable
    command. ``shlex.quote`` is a no-op for paths with no special
    characters, so the snapshot stays stable for plain ASCII paths.

    Args:
        disclosure: Pre-dispatch summary built by
            :func:`tools.cross_ai.disclosure.build_disclosure`.
        prompt_path: Absolute path to the prompt file the dispatcher
            wrote (typically the return of :func:`write_prompt_to_disk`).
        paste_back_command: Override for the paste-back hint. ``None``
            (the default) renders the canonical
            ``/forge:review --cross-ai-paste response.md`` literal.

    Returns:
        Multi-line plain-text snapshot ready to be printed to stdout.
    """
    paste_command = (
        paste_back_command if paste_back_command is not None else _DEFAULT_PASTE_BACK_COMMAND
    )
    quoted_prompt_path = shlex.quote(str(prompt_path))
    quoted_response = shlex.quote("response.md")
    return (
        "Cross-AI review (manual mode) — review before sending\n"
        f"  Target:           {disclosure.target.value}\n"
        f"  Reviewer CLI:     {disclosure.cli.value}\n"
        f"  Files referenced: {len(disclosure.file_list)}\n"
        f"  Files excluded:   {len(disclosure.excluded_files)} (redaction)\n"
        f"  Diff LOC:         {disclosure.diff_loc}\n"
        f"  Prompt tokens:    {disclosure.prompt_tokens} (estimate)\n"
        f"  Estimated cost:   ${disclosure.prompt_cost_usd:.4f} (advisory)\n"
        f"  Cost warn:        {_yes_no(disclosure.cost_warn_triggered)}\n"
        f"  Redactions:       {_yes_no(disclosure.had_redactions)}\n"
        f"  Command preview:  {disclosure.command_preview}\n"
        f"  Prompt path:      {prompt_path}\n"
        "\n"
        "  Run externally:\n"
        f"    {disclosure.cli.value} < {quoted_prompt_path} > {quoted_response}\n"
        "\n"
        "  Then paste back:\n"
        f"    {paste_command}"
    )

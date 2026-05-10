"""Manual-mode orchestration helpers for the cross-AI substrate.

The skill (``forge-review``) drives every cross-AI manual cycle from the
conversation; this module is the mechanical layer the skill calls into.
Each helper performs a single, self-contained filesystem or formatting
step so the skill never has to inline path arithmetic or table-row
escaping.

Public surface (in ``__all__``)
-------------------------------

* :func:`write_prompt_to_disk` — atomically write a built reviewer
  ``Prompt.body`` to ``.forge/features/<id>/cross-ai/<target>-<utc>-prompt.md``
  and return the absolute path. ``now=`` is injectable so tests can lock
  the timestamp portion of the filename.
* :func:`read_paste_response` — UTF-8 read of a pasted reviewer
  response. Errors propagate (``FileNotFoundError`` / ``UnicodeDecodeError``)
  so the skill can surface a precise failure rather than a silent miss.
* :func:`merge_findings_into_review` — append parsed
  :class:`tools.cross_ai.parse.Finding` rows to the existing
  ``REVIEW.<target>.md`` table. Empty input is a fast-path no-op; a
  missing review file is fatal so the skill knows it forgot the
  template-copy step.
* :func:`format_disclosure_summary` — render the pre-dispatch
  :class:`tools.cross_ai.disclosure.Disclosure` snapshot as the
  multi-line plain-text block the dispatcher prints to the operator.
  The text is a stable contract (snapshot-tested) — every visible
  newline below is part of the agreed format.

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
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from tools.cross_ai.disclosure import Disclosure
from tools.cross_ai.parse import Finding
from tools.cross_ai.prompt import Prompt, PromptTarget

__all__ = (
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
    prompt: Prompt,
    feature_id: str,
    repo_root: Path,
    *,
    now: datetime | None = None,
) -> Path:
    """Persist ``prompt.body`` to the cross-AI prompt directory.

    The destination is
    ``<repo_root>/.forge/features/<feature_id>/cross-ai/<target>-<utc>-prompt.md``.
    The parent directory is created if absent so the skill never has to
    pre-seed it.

    Args:
        prompt: Built reviewer prompt — ``prompt.body`` is what hits
            disk; ``prompt.target`` drives the filename prefix.
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
    prompt_path = target_dir / f"{prompt.target.value}-{timestamp}-prompt.md"
    _atomic_write_text(prompt_path, prompt.body, prefix=".cross-ai-prompt-")
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


def _escape_pipes(value: str) -> str:
    r"""Escape literal ``|`` characters so a Markdown table row stays well-formed.

    A pipe inside a cell would split it; the standard escape is ``\|``.
    Already-escaped pipes (``\|``) are preserved verbatim — we only
    target *unescaped* pipes so a second merge of the same content does
    not double-escape the previous merge's output.
    """
    out: list[str] = []
    index = 0
    length = len(value)
    while index < length:
        char = value[index]
        if char == "\\" and index + 1 < length and value[index + 1] == "|":
            # Already-escaped pipe — pass both characters through untouched.
            out.append("\\|")
            index += 2
            continue
        if char == "|":
            out.append("\\|")
            index += 1
            continue
        out.append(char)
        index += 1
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
        if line.strip().lower() == "# findings":
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
        f"    {disclosure.cli.value} < {prompt_path} > response.md\n"
        "\n"
        "  Then paste back:\n"
        f"    {paste_command}"
    )

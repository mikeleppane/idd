"""Cross-feature trap memory: lessons artifact parser, allocator, append, amend.

Public surface lives in this module; ``tools.intel.__init__`` re-exports.
The parser is independent of ``tools.constitution`` — :class:`Lesson` is its
own type and the strip-code idiom is copied (not imported) so the two
modules can evolve separately.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Final, Literal, cast

from tools._relevance import RelevanceError, RelevanceRule, score_and_trim
from tools.constitution import tokenize
from tools.constitution_amend import atomic_replace

# fcntl is POSIX-only. Skip the advisory lock when unavailable (Windows) — the
# rest of the module works regardless; we fall back to a read-and-recheck race
# narrow at the append call site.
fcntl: ModuleType | None
try:
    import fcntl as _fcntl_mod

    fcntl = _fcntl_mod
except ModuleNotFoundError:  # pragma: no cover - non-POSIX (Windows)
    fcntl = None

_TITLE_MAX_LEN: Final[int] = 80
_TITLE_TRUNCATE_AT: Final[int] = 77

LessonSeverity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
LessonStatus = Literal["active", "retired"]  # "superseded-by:L<NNN>" handled separately

# Frozen, controlled tag vocabulary. Free-form tags are rejected by the
# parser. Extending the set is a deliberate code change with a matching test
# row — drift makes the relevance scorer noisy.
_TAG_VOCAB: frozenset[str] = frozenset(
    {
        "imports",
        "fixtures",
        "state-mutation",
        "async",
        "secrets",
        "validation",
        "dispatch",
        "review-tagging",
        "ship-gate",
        "cross-ai",
        "bdd",
        "frontmatter",
    }
)

_VALID_SEVERITIES: frozenset[str] = frozenset({"CRITICAL", "HIGH", "MEDIUM", "LOW"})
_TERMINAL_STATUSES: frozenset[str] = frozenset({"active", "retired"})

# Body block regexes. The header captures only the L<NNN> id; structural
# checks for the id digit count happen against the captured token.
_ID_RE = re.compile(r"^L\d{3}$")
# Header anchors the id to exactly three digits so a one-digit ``L7`` or a
# four-digit ``L9999`` is rejected at the header pass with a single clear
# error. The downstream ``_ID_RE`` check stays as belt-and-braces.
_HEADER_RE = re.compile(r"^## (L\d{3})\s+—\s+(.+?)\s*$")
_FIELD_RE = re.compile(
    r"^\*\*(Captured|Resolved by|Trap|Avoidance|Tags|Severity|Status):\*\*\s*(.*)$"
)
_FIELD_KEYS = ("captured", "resolved by", "trap", "avoidance", "tags", "severity", "status")
# Only one marker name needs renaming when mapped to the internal field key —
# the rest are identity. Keep the rename table small and explicit instead of
# spelling out six identity entries that obscure the actual transformation.
_FIELD_RENAME: dict[str, str] = {"resolved by": "resolved_by"}
_REQUIRED_FIELDS = ("captured", "resolved_by", "trap", "avoidance", "tags", "severity", "status")

_CAPTURED_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+from\s+feature\s+(\S+)\s*$")
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# Case-insensitive detector used to gate SHA normalization at parse time so
# uppercase / mixed-case hex from a git GUI normalizes to lowercase before
# the strict ``_SHA_RE`` check. Symmetric with ``ship_gate._HEX_40_RE``.
_SHA_RE_INSENSITIVE = re.compile(r"^[0-9a-fA-F]{40}$")
_SUPERSEDED_RE = re.compile(r"^superseded-by:(L\d{3})$")

# Strip fenced + inline code so illustrative ``## L001`` lines inside code
# blocks cannot register as phantom lessons. Pattern is copied from
# ``tools.constitution._strip_code_regions`` rather than imported so the two
# loaders evolve independently.
_FENCE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")

_DEFAULT_HEADER = '---\nversion: 0.1.0\ncreated: "{created}"\n---\n\n# FORGE Lessons\n\n'

# Authoring cap on individual Trap / Avoidance prose surfaced at the parser
# level. The interactive skill (forge-lesson) enforces this at the UI prompt,
# and the parser also enforces it so a malformed file is rejected with a
# message that points at the offending line and field. Direct construction
# via ``Lesson(...)`` bypasses the parser, so the dataclass also defends a
# looser back-door cap (:data:`_MAX_LESSON_FIELD_CHARS`) — see
# :meth:`Lesson.__post_init__`. The dispatch budget cap (MAX_LESSON_WORDS) is
# independent: it controls cumulative budget at filter time, not per-entry
# authoring length.
_MAX_FIELD_CHARS: Final[int] = 1000

# Back-door cap enforced by ``Lesson.__post_init__`` for direct construction
# paths that bypass the parser (tests, future CLIs, library users). Sized
# above the parser's 1000-char cap so a caller stamping ~1500 chars during
# integration testing does not have to wrap every fixture in a parser round
# trip, but tight enough that a stray 5000-char paste still fails loudly.
_MAX_LESSON_FIELD_CHARS: Final[int] = 2000

MAX_LESSON_WORDS: Final[int] = 600
"""Dispatch-budget cap for ``lessons[]``.

Sized at roughly half of :data:`tools.constitution.MAX_INJECTED_WORDS`
(1153 ~= 1500 tokens / 1.3 words-per-token). Articles describe the project's
durable rules and earn the larger budget; lessons describe transient
failure modes and pay the smaller cap. Keeping the two caps independent
means a heavy lesson load cannot squeeze CRITICAL Constitution articles
out of their own budget — the caller injects both lists side-by-side and
each list pays its own cap.

Revisit if real-world dispatches consistently report lessons being trimmed
at the cap. Until then, 600 words ~= 780 tokens stays comfortable inside
the ~3000-token total subagent context overhead.
"""

# Refuse to read a lessons file larger than this cap. A typical lessons.md
# holds a handful of entries at a few hundred chars each; 1 MiB is several
# orders of magnitude past plausible content and guards against an accidental
# (or hostile) multi-GB file from blowing up the parser's two regex passes and
# splitlines list at ~3x peak memory.
_MAX_LESSONS_FILE_BYTES: Final[int] = 1 << 20


class LessonError(RuntimeError):
    """Raised when the lessons artifact cannot be parsed, allocated, or amended."""


def _read_lessons_file(path: Path) -> str:
    """Read ``path`` enforcing :data:`_MAX_LESSONS_FILE_BYTES`."""
    size = path.stat().st_size
    if size > _MAX_LESSONS_FILE_BYTES:
        raise LessonError(
            f"lessons file at {path} is {size} bytes; refuse to parse "
            f"a file larger than {_MAX_LESSONS_FILE_BYTES} bytes "
            "(suspected malformed or out-of-scope content)"
        )
    return path.read_text(encoding="utf-8")


@dataclass(frozen=True, kw_only=True)
class Lesson:
    """One parsed lesson entry from ``.forge/intel/lessons.md``."""

    id: str
    captured: date
    captured_from: str
    resolved_by: str
    trap: str
    avoidance: str
    tags: tuple[str, ...]
    severity: LessonSeverity
    status: str
    body_words: int

    def __post_init__(self) -> None:
        """Defend per-entry length bounds for direct construction callers.

        The parser already enforces a tighter 1000-char authoring cap (see
        :data:`_MAX_FIELD_CHARS`) and surfaces line-number context. Direct
        ``Lesson(...)`` callers — tests, future CLIs, library users — skip
        that path, so the dataclass guards a looser back-door cap of
        :data:`_MAX_LESSON_FIELD_CHARS` per field plus a combined-word cap
        aligned to :data:`MAX_LESSON_WORDS` so a single bloated lesson
        cannot crash the dispatch-budget filter at load time.
        """
        if len(self.trap) > _MAX_LESSON_FIELD_CHARS:
            raise LessonError(
                f"lesson {self.id!r}: trap exceeds {_MAX_LESSON_FIELD_CHARS} chars "
                f"(got {len(self.trap)})"
            )
        if len(self.avoidance) > _MAX_LESSON_FIELD_CHARS:
            raise LessonError(
                f"lesson {self.id!r}: avoidance exceeds {_MAX_LESSON_FIELD_CHARS} chars "
                f"(got {len(self.avoidance)})"
            )
        combined_words = len(self.trap.split()) + len(self.avoidance.split())
        if combined_words > MAX_LESSON_WORDS:
            raise LessonError(
                f"lesson {self.id!r}: trap+avoidance exceeds {MAX_LESSON_WORDS} words "
                f"(got {combined_words}); trim before append"
            )

    def to_budget_dict(self) -> dict[str, object]:
        """Return the locked JSON shape consumed by the dispatch budget injection.

        Mirrors :meth:`tools.constitution.Article.to_budget_dict` — ``body_words``
        is loader-internal and never leaks into subagent prompts.
        """
        return {
            "id": self.id,
            "trap": self.trap,
            "avoidance": self.avoidance,
            "tags": list(self.tags),
            "severity": self.severity,
            "status": self.status,
        }


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _strip_code_regions(text: str) -> str:
    """Blank fenced + inline code spans with same-length whitespace.

    Whitespace replacement keeps byte offsets stable so any future
    line-number reporting matches the original file.
    """
    out = _FENCE_BLOCK_RE.sub(lambda m: " " * len(m.group(0)), text)
    return _INLINE_CODE_RE.sub(lambda m: " " * len(m.group(0)), out)


@dataclass
class _ParseState:
    current: dict[str, object] | None = None
    active_field: str | None = None


def parse(path: Path) -> list[Lesson]:
    """Parse ``.forge/intel/lessons.md``. Missing file returns ``[]``.

    Validation order (each step raises :class:`LessonError` naming the
    offending entry / field):

    1. File missing -> empty list.
    2. Per-entry header + field shape (id, captured, resolved-by, tags,
       severity, status).
    3. Cross-entry checks (unique ids, monotonic ascending id order,
       superseded targets exist and are not already superseded).

    Monotonic order is structural: :func:`append` always lands at end of
    file, so the natural growth direction is ascending. Manually reordering
    entries (e.g. alphabetical sort) makes the file unparseable and locks
    the writer out of :func:`amend_status` until the original order is
    restored. Re-sort by ``L<NNN>`` numerically if a reorder is necessary.
    """
    if not path.exists():
        return []
    return parse_text(_read_lessons_file(path))


def parse_text(text: str) -> list[Lesson]:
    """In-memory counterpart to :func:`parse`. Same validation rules.

    Per-block error messages include the 1-based source line of the lesson
    header so authors can navigate to the offending entry directly.
    """
    scrubbed = _strip_code_regions(text)
    state = _ParseState()
    blocks: list[dict[str, object]] = []
    for line_idx, raw_line in enumerate(scrubbed.splitlines(), start=1):
        _consume_line(raw_line, state, blocks, line_no=line_idx)
    if state.current is not None:
        blocks.append(state.current)

    lessons = [_block_to_lesson(block) for block in blocks]
    _check_cross_entry(lessons)
    return lessons


def _consume_line(
    line: str,
    state: _ParseState,
    blocks: list[dict[str, object]],
    *,
    line_no: int,
) -> None:
    if line.startswith("## L"):
        match = _HEADER_RE.match(line)
        if not match:
            raise LessonError(f"line {line_no}: malformed lesson header: {line!r}")
        lesson_id = match.group(1)
        if not _ID_RE.match(lesson_id):
            raise LessonError(
                f"line {line_no}: malformed lesson id {lesson_id!r}: expected L<NNN> zero-padded"
            )
        if state.current is not None:
            blocks.append(state.current)
        state.current = {
            "id": lesson_id,
            "title": match.group(2).strip(),
            "captured": "",
            "resolved_by": "",
            "trap": "",
            "avoidance": "",
            "tags": "",
            "severity": "",
            "status": "",
            "_header_line": line_no,
        }
        state.active_field = None
        return

    if state.current is None:
        return

    field_match = _FIELD_RE.match(line)
    if field_match:
        marker = field_match.group(1).lower()
        if marker not in _FIELD_KEYS:
            state.active_field = None
            return
        internal = _FIELD_RENAME.get(marker, marker)
        state.active_field = internal
        state.current[internal] = field_match.group(2).strip()
        return

    if state.active_field is None:
        return

    stripped = line.strip()
    if not stripped:
        state.active_field = None
        return
    existing = cast(str, state.current.get(state.active_field, ""))
    state.current[state.active_field] = f"{existing} {stripped}".strip() if existing else stripped


def _block_to_lesson(block: dict[str, object]) -> Lesson:
    lesson_id = _expect_str(block, "id")
    # The header-line number is stamped into the block by _consume_line so
    # every per-block error message can point the author at the source row.
    header_line = block.get("_header_line", "?")
    prefix = f"line {header_line} lesson {lesson_id}"

    for field in _REQUIRED_FIELDS:
        value = block.get(field, "")
        if not isinstance(value, str) or not value.strip():
            raise LessonError(
                f"{prefix}: missing required field `{field.replace('_', ' ').capitalize()}`"
            )

    captured_raw = _expect_str(block, "captured")
    captured_match = _CAPTURED_RE.match(captured_raw)
    if not captured_match:
        raise LessonError(
            f"{prefix}: Captured line must match "
            f"'YYYY-MM-DD from feature <id>', got {captured_raw!r}"
        )
    try:
        captured = date.fromisoformat(captured_match.group(1))
    except ValueError as exc:
        raise LessonError(f"{prefix}: Captured date not ISO-parseable: {exc}") from exc
    captured_from = captured_match.group(2)

    resolved_by = _expect_str(block, "resolved_by")
    # SHA normalization: a contributor copying a SHA from a git GUI may land
    # mixed-case hex. Lowercase here at parse time so downstream comparisons
    # (commit lookup, harvest-hook key match) stay deterministic regardless
    # of the input casing. Symmetric with the ``parse_review_findings`` fix
    # in ``tools.ship_gate``. The literal ``manual`` is case-sensitive by
    # design and stored verbatim.
    if resolved_by != "manual" and _SHA_RE_INSENSITIVE.match(resolved_by):
        resolved_by = resolved_by.lower()
    if resolved_by != "manual" and not _SHA_RE.match(resolved_by):
        raise LessonError(
            f"{prefix}: Resolved-by must be 40-hex SHA or 'manual', got {resolved_by!r}"
        )

    tags_raw = _expect_str(block, "tags")
    tags = _parse_tags(prefix, tags_raw)

    severity = _expect_str(block, "severity")
    if severity not in _VALID_SEVERITIES:
        raise LessonError(f"{prefix}: Severity {severity!r} not in {sorted(_VALID_SEVERITIES)}")

    status = _expect_str(block, "status")
    _validate_status_shape(prefix, status)

    trap = _expect_str(block, "trap")
    avoidance = _expect_str(block, "avoidance")
    for field_name, field_value in (("Trap", trap), ("Avoidance", avoidance)):
        if len(field_value) > _MAX_FIELD_CHARS:
            raise LessonError(
                f"{prefix}: {field_name} field is {len(field_value)} chars; "
                f"cap is {_MAX_FIELD_CHARS}. Tighten the prose; future readers must "
                "be able to scan the row in the dispatch budget summary."
            )
    body_words = len((trap + " " + avoidance).split())

    return Lesson(
        id=lesson_id,
        captured=captured,
        captured_from=captured_from,
        resolved_by=resolved_by,
        trap=trap,
        avoidance=avoidance,
        tags=tags,
        severity=severity,  # type: ignore[arg-type]
        status=status,
        body_words=body_words,
    )


def _expect_str(block: dict[str, object], key: str) -> str:
    value = block.get(key, "")
    if not isinstance(value, str):
        raise LessonError(f"internal: field {key!r} must be string, got {type(value).__name__}")
    return value


def _parse_tags(ctx: str, raw: str) -> tuple[str, ...]:
    """Parse the comma-separated Tags cell against the controlled vocabulary.

    Vocabulary matching is case-insensitive: ``Dispatch`` and ``DISPATCH``
    both resolve to the canonical lowercase ``dispatch`` form stored on the
    Lesson. Error messages preserve the author's original spelling so the
    offending row stays greppable in the source file.

    ``ctx`` is a free-form caller-supplied prefix prepended to every error
    message — typically the per-block ``line N lesson L001`` string or, when
    invoked from a non-parser caller, the raw ``lesson L001``.
    """
    raw_tokens = [t.strip() for t in raw.split(",") if t.strip()]
    if not raw_tokens:
        raise LessonError(f"{ctx}: Tags list is empty; at least one tag required")
    seen: list[str] = []
    for raw_tok in raw_tokens:
        canonical = raw_tok.casefold()
        if canonical not in _TAG_VOCAB:
            raise LessonError(
                f"{ctx}: tag {raw_tok!r} not in controlled vocabulary "
                f"(allowed: {sorted(_TAG_VOCAB)})"
            )
        if canonical not in seen:
            seen.append(canonical)
    return tuple(seen)


def _validate_status_shape(ctx: str, status: str) -> None:
    """Validate that ``status`` matches the closed lesson-status vocabulary.

    ``ctx`` is a caller-supplied prefix prepended to every error message.
    """
    if status in _TERMINAL_STATUSES:
        return
    if status.startswith("superseded-by:"):
        if not _SUPERSEDED_RE.match(status):
            raise LessonError(
                f"{ctx}: Status superseded target must be L<NNN> "
                f"(three zero-padded digits), got {status!r}"
            )
        return
    raise LessonError(
        f"{ctx}: Status {status!r} not in "
        f"{sorted(_TERMINAL_STATUSES)} and not a 'superseded-by:L<NNN>' marker"
    )


def _check_cross_entry(lessons: list[Lesson]) -> None:
    by_id: dict[str, Lesson] = {}
    for lesson in lessons:
        if lesson.id in by_id:
            raise LessonError(f"duplicate lesson id {lesson.id}")
        by_id[lesson.id] = lesson

    last_num = -1
    for lesson in lessons:
        num = int(lesson.id[1:])
        if num <= last_num:
            raise LessonError(
                f"lessons must appear in monotonic id order: {lesson.id} "
                f"follows id with number {last_num}"
            )
        last_num = num

    for lesson in lessons:
        match = _SUPERSEDED_RE.match(lesson.status)
        if not match:
            continue
        target_id = match.group(1)
        if target_id == lesson.id:
            raise LessonError(f"lesson {lesson.id}: superseded-by target cannot be self")
        target = by_id.get(target_id)
        if target is None:
            raise LessonError(
                f"lesson {lesson.id}: superseded-by target {target_id} not found in file"
            )
        if _SUPERSEDED_RE.match(target.status):
            raise LessonError(
                f"lesson {lesson.id}: superseded-by:{target_id} forms a chain; "
                f"{target_id} is itself superseded ({target.status})"
            )


# ---------------------------------------------------------------------------
# Allocator
# ---------------------------------------------------------------------------


def _lessons_path(repo_root: Path) -> Path:
    return repo_root / ".forge" / "intel" / "lessons.md"


def next_id(repo_root: Path) -> str:
    """Return the next free ``L<NNN>`` slot for the lessons file.

    Missing file -> ``L001``. Malformed file -> :class:`LessonError`
    (the allocator refuses to skip over a broken file; the caller fixes it
    first).
    """
    path = _lessons_path(repo_root)
    lessons = parse(path)
    if not lessons:
        return "L001"
    max_num = max(int(le.id[1:]) for le in lessons)
    return f"L{max_num + 1:03d}"


# ---------------------------------------------------------------------------
# Append + amend helpers
# ---------------------------------------------------------------------------


def _serialize_lesson(lesson: Lesson) -> str:
    title = (lesson.trap.splitlines()[0] if lesson.trap else "Untitled").strip()
    if len(title) > _TITLE_MAX_LEN:
        title = title[:_TITLE_TRUNCATE_AT] + "..."
    tags_csv = ", ".join(lesson.tags)
    return (
        f"## {lesson.id} — {title}\n"
        f"**Captured:** {lesson.captured.isoformat()} from feature {lesson.captured_from}\n"
        f"**Resolved by:** {lesson.resolved_by}\n"
        f"**Trap:** {lesson.trap}\n"
        f"**Avoidance:** {lesson.avoidance}\n"
        f"**Tags:** {tags_csv}\n"
        f"**Severity:** {lesson.severity}\n"
        f"**Status:** {lesson.status}\n"
    )


def append(repo_root: Path, draft: Lesson, *, today: date | None = None) -> Path:
    """Append a fresh lesson to ``.forge/intel/lessons.md``.

    Refuses when ``draft.id`` does not equal :func:`next_id` (caller cannot
    skip a slot). Atomic write via
    :func:`tools.constitution_amend.atomic_replace`.

    The ``today`` parameter only stamps the auto-generated frontmatter header
    when the lessons file does not yet exist. Once the file exists each
    subsequent ``append`` call leaves the ``created:`` line untouched — the
    parameter is silently ignored on those paths so the signature stays
    stable for callers (test fixtures, scripted CLIs) that always pass it.
    Each entry's ``Captured:`` line always reflects ``draft.captured`` and
    is independent of ``today``.

    Concurrency:
        Holds an advisory ``fcntl.LOCK_EX | LOCK_NB`` on a sidecar
        ``.forge/intel/lessons.md.lock`` for the duration of the call so two
        concurrent appenders cannot both compute the same :func:`next_id` and
        silently overwrite each other's lesson. The lock is opt-in: when
        ``fcntl`` is unavailable (Windows), a deliberate single-author retry is
        substituted — :func:`next_id` is re-derived just before
        :func:`atomic_replace` and the call refuses with :class:`LessonError`
        on slot drift instead of clobbering. Either path turns a silent data
        loss into a loud failure the caller can retry.
    """
    today = today or date.today()
    path = _lessons_path(repo_root)
    lock_path = path.with_suffix(path.suffix + ".lock")
    # Touch the lockfile so flock has something to attach to even on a fresh
    # repo where lessons.md itself does not exist yet. The lockfile is a
    # sidecar artefact owned by this writer; safe to create unconditionally.
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = None
    try:
        if fcntl is not None:
            lock_fh = lock_path.open("w")
            try:
                fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                lock_fh.close()
                lock_fh = None
                raise LessonError(
                    "another lesson append is in flight; retry after the "
                    "concurrent writer completes"
                ) from exc

        expected = next_id(repo_root)
        if draft.id != expected:
            raise LessonError(
                f"append rejected: draft.id={draft.id!r} but next free slot is "
                f"{expected!r}; allocator forbids skipping ids"
            )

        if path.exists():
            prior_text = _read_lessons_file(path)
            # parse() already ran inside next_id(); reuse the file body verbatim.
            new_text = _append_to_body(prior_text, draft)
        else:
            header = _DEFAULT_HEADER.format(created=today.isoformat())
            new_text = header + _serialize_lesson(draft)

        # Round-trip validation: ensure the merged body still parses cleanly.
        parse_text(new_text)

        # Race narrow for the no-fcntl fallback path. Re-derive next_id once
        # more between the body build and the rename; on slot drift refuse
        # to clobber. Under fcntl this check is a defensive belt-and-braces
        # — the exclusive lock already guarantees no concurrent writer
        # advanced the file.
        post_expected = next_id(repo_root)
        if post_expected != expected:
            raise LessonError(
                f"concurrent append detected: slot {expected!r} was filled by "
                f"another writer (next free now {post_expected!r}); retry "
                "append after re-deriving next_id"
            )

        atomic_replace(path, new_text)
        return path
    finally:
        if lock_fh is not None:
            lock_fh.close()


def _append_to_body(prior_text: str, draft: Lesson) -> str:
    serialized = _serialize_lesson(draft)
    # Guarantee a separating blank line before the new entry.
    if not prior_text.endswith("\n"):
        prior_text += "\n"
    if not prior_text.endswith("\n\n"):
        prior_text += "\n"
    return prior_text + serialized


def amend_status(
    repo_root: Path,
    lesson_id: str,
    new_status: str,
) -> Path:
    """Flip an existing lesson's Status field.

    Allowed transitions:

    - ``active`` -> ``retired``
    - ``active`` -> ``superseded-by:L<NNN>``
    - ``retired`` -> ``active``
    - ``retired`` -> ``superseded-by:L<NNN>``

    Rejects missing ``lesson_id``, bad ``new_status`` shape, missing
    superseded targets, and chains (target already superseded).
    """
    path = _lessons_path(repo_root)
    if not path.exists():
        raise LessonError(f"lessons file not found at {path}")

    lessons = parse(path)
    by_id = {le.id: le for le in lessons}
    if lesson_id not in by_id:
        raise LessonError(f"lesson {lesson_id} not present in {path}")

    _validate_status_shape(f"lesson {lesson_id}", new_status)

    current = by_id[lesson_id]
    _check_transition(current.status, new_status)
    _check_superseded_target(by_id, lesson_id, new_status)

    new_body = _rewrite_status(
        _read_lessons_file(path),
        lesson_id=lesson_id,
        new_status=new_status,
    )
    # Round-trip validation against the rewritten body.
    parse_text(new_body)
    atomic_replace(path, new_body)
    return path


def _check_transition(current: str, new_status: str) -> None:
    if current == new_status:
        raise LessonError(f"status already {current!r}; no transition")
    if _SUPERSEDED_RE.match(current):
        raise LessonError(f"cannot amend an already-superseded lesson (current status {current!r})")
    if current not in _TERMINAL_STATUSES:
        # Defensive — parser should have rejected.
        raise LessonError(f"unsupported current status {current!r}")


def _check_superseded_target(
    by_id: dict[str, Lesson],
    lesson_id: str,
    new_status: str,
) -> None:
    match = _SUPERSEDED_RE.match(new_status)
    if not match:
        return
    target_id = match.group(1)
    if target_id == lesson_id:
        raise LessonError(f"lesson {lesson_id}: cannot supersede itself")
    target = by_id.get(target_id)
    if target is None:
        raise LessonError(f"superseded-by target {target_id} not found in lessons file")
    if _SUPERSEDED_RE.match(target.status):
        raise LessonError(
            f"superseded-by:{target_id} forms a chain; {target_id} status is {target.status!r}"
        )


def _rewrite_status(text: str, *, lesson_id: str, new_status: str) -> str:
    """Replace the ``**Status:**`` line of the named lesson block.

    Walks the file in block order so a ``Status:`` line inside the wrong
    entry's body cannot be flipped. Raises :class:`LessonError` if the
    target block carries no parseable status line (parser would also catch).
    """
    lines = text.splitlines(keepends=True)
    in_target = False
    rewrote = False
    out: list[str] = []
    header_prefix = f"## {lesson_id} "
    for raw in lines:
        if raw.startswith("## L"):
            in_target = raw.startswith(header_prefix)
        if in_target and not rewrote and raw.lstrip().startswith("**Status:**"):
            out.append(f"**Status:** {new_status}\n")
            rewrote = True
            continue
        out.append(raw)
    if not rewrote:
        raise LessonError(f"could not locate Status line for {lesson_id} during rewrite")
    return "".join(out)


# ---------------------------------------------------------------------------
# Dispatch-budget loader
# ---------------------------------------------------------------------------


# Severity -> bucket map for the relevance gate. CRITICAL lessons are always
# kept; HIGH gates at the 25th percentile; MEDIUM and LOW gate at the median.
# Mirrors the CRITICAL/SHOULD/MAY shape of the Constitution filter but uses
# the Lesson vocabulary.
_LESSON_SEVERITY_BUCKET: dict[str, Literal["always_kept", "p25_gate", "median_gate"]] = {
    "CRITICAL": "always_kept",
    "HIGH": "p25_gate",
    "MEDIUM": "median_gate",
    "LOW": "median_gate",
}


def _score_lesson(lesson: Lesson, scope_keywords: set[str]) -> int:
    """Count tag tokens that overlap ``scope_keywords``.

    Tags are already lowercase by parser contract; ``scope_keywords`` is
    lowercase per ``tools.constitution.tokenize``'s contract. The set
    intersection therefore needs no extra normalisation.

    Args:
        lesson: Lesson to score.
        scope_keywords: Pre-tokenized scope keyword set.

    Returns:
        Overlap count (>= 0).
    """
    return len(set(lesson.tags) & scope_keywords)


def load_and_filter(
    repo_root: Path,
    *,
    idea_text: str = "",
    files_in_scope: Iterable[Path] = (),
) -> tuple[list[Lesson], list[str]]:
    """Parse ``.forge/intel/lessons.md``, filter to active + relevant lessons.

    Returns ``([], [])`` when the lessons file is absent (fresh repo).

    Filtering steps:

    1. :func:`parse` yields all lessons.
    2. Drop ``retired`` and ``superseded-by:*`` rows (status filter). Those
       ids do NOT appear in the returned ``dropped_ids`` list — that list is
       reserved for relevance-dropped lessons. Status-dropped lessons are
       silent because the dispatch budget never wanted to inject them.
    3. :func:`tools._relevance.score_and_trim` applies the percentile
       gate and the :data:`MAX_LESSON_WORDS` cap. Severity -> bucket comes
       from ``_LESSON_SEVERITY_BUCKET``.
    4. ``scope_keywords`` are derived via the same tokenizer the Constitution
       filter uses (``tools.constitution.tokenize``): a union of tokens from
       ``idea_text`` and from each ``files_in_scope`` path's string form.

    Args:
        repo_root: Repository root containing ``.forge/intel/lessons.md``.
        idea_text: Free-form idea / spec intent text.
        files_in_scope: Paths to include as scope signals.

    Returns:
        Tuple of (kept_lessons, dropped_lesson_ids). ``kept`` is ordered by
        lesson numbering (L001 first, L002 next, ...); ``dropped`` carries
        only the relevance-dropped ids in the same order.

    Raises:
        LessonError: When CRITICAL active lessons alone exceed
            :data:`MAX_LESSON_WORDS`. The author must trim Trap/Avoidance
            bodies, demote some to HIGH, or retire stale entries.
    """
    path = _lessons_path(repo_root)
    if not path.exists():
        return [], []

    lessons = parse(path)
    active = [le for le in lessons if le.status == "active"]
    if not active:
        return [], []

    scope_keywords: set[str] = set()
    scope_keywords |= tokenize(idea_text)
    for fp in files_in_scope:
        scope_keywords |= tokenize(str(fp))

    rule: RelevanceRule[Lesson] = RelevanceRule(
        score=lambda le: _score_lesson(le, scope_keywords),
        level_of=lambda le: le.severity,
        body_words_of=lambda le: le.body_words,
        id_of=lambda le: le.id,
        level_bucket=dict(_LESSON_SEVERITY_BUCKET),
        max_words=MAX_LESSON_WORDS,
    )
    try:
        return score_and_trim(active, rule=rule)
    except RelevanceError as exc:
        critical_ids = [le.id for le in active if le.severity == "CRITICAL"]
        total = sum(le.body_words for le in active if le.severity == "CRITICAL")
        raise LessonError(
            f"CRITICAL lessons {critical_ids} exceed the {MAX_LESSON_WORDS}-word "
            f"dispatch budget on their own ({total} words). Trim Trap/Avoidance "
            f"bodies, demote some to HIGH, or retire stale entries."
        ) from exc

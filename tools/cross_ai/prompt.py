"""Per-target reviewer prompt builder for the cross-AI substrate.

Builds a self-contained Markdown prompt the dispatcher hands to an
external reviewer CLI. Two branches:

* ``target=plan`` — feeds the reviewer SPEC excerpts plus the verbatim
  PLAN.md so it can flag missing acceptance coverage, ambiguous slice
  boundaries, and constitution violations *before* any code is written.
* ``target=code`` — feeds SPEC excerpts plus a ``git diff --stat``
  header and per-file diffs covering every commit recorded in
  ``state.commits[]``. The reviewer sees the same change-set the
  feature owner is about to ship.

Pre-redaction: this module deliberately performs **no** secret
filtering. The dispatcher (P2/P3) calls
``tools.redaction.filter()`` over the returned ``Prompt.body`` plus the
``files_referenced`` list before any external dispatch. Splitting the
two phases lets the redactor reason about a single Markdown blob with a
known shape rather than racing the builder.

Section names — tolerant prefix match. Real FORGE SPEC.md files use
``## Acceptance Criteria`` / ``## Negative Requirements``; UNDERSTANDING
files use ``# Pre-Mortem (Top Failure Modes)``. The plan §P1.4 contract
quotes the canonical short forms (``# Acceptance``, ``# Negative
Requirements``, ``# Intent``, ``# Pre-Mortem``); the extractor matches
any header line whose stripped ``#`` prefix begins with those tokens so
both spellings flow through without per-feature fixups.

Constitution injection — calls ``tools.constitution.load_and_filter``
with the spec ``Intent`` (truncated to ≤200 words per plan step 1) as
``idea_text`` and ``files_referenced`` as ``files_in_scope``. The
filter applies the M3 D-9 minimal-relevance rule and the 1500-token
cap; we serialize the survivors as a Markdown list and append.

Reviewer mandate — explicit Markdown table format spec. The
dispatcher's parser (P2/P3) ingests rows shaped exactly as the mandate
documents, so any wording drift here breaks the parse downstream.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from tools.constitution import Article, load_and_filter
from tools.state import read_state

# Intent body cap per plan step 1 — the spec Intent often runs long
# enough to crowd out the rest of the prompt; 200 words preserves the
# *why* without crowding the slice list / diff.
_INTENT_WORD_CAP: int = 200

# Files in scope: <comma list>  — extracted line-by-line, not via regex
# over the whole feature directory (per discipline rule "no regex over
# feature directory"). The marker is parsed as a literal prefix on the
# line after stripping leading Markdown emphasis (``**``).
_FILES_IN_SCOPE_MARKER: str = "Files in scope:"

# Reviewer mandate footer — frozen verbatim so the P2/P3 parser sees a
# stable contract. Severities and Status defaults are part of the spec
# §5.3.3 step 2 row shape.
_REVIEWER_MANDATE: str = """\
## Reviewer Mandate

Return findings as a Markdown table with columns:
ID | Severity | Status | Location | Problem | Fix | Source.

- Use severities BLOCK / HIGH / MEDIUM / LOW / INFO.
- Status defaults to `open`.
- Tag Constitution-related findings with `[constitution:A<n>]` in the
  Source column so the dispatcher can route them back to the originating
  Article.
"""


class PromptTarget(StrEnum):
    """Reviewer prompt target. Matches ``state.review.current_target``."""

    plan = "plan"
    code = "code"


@dataclass(frozen=True)
class Prompt:
    """Self-contained reviewer prompt + the file paths it references.

    ``body`` is Markdown ready for dispatch (after caller-side
    redaction). ``files_referenced`` is the union of every file path the
    body discusses — the redactor uses it to widen its overlay so the
    same paths get scrubbed even if they happen to slip the body's
    pattern set.
    """

    target: PromptTarget
    feature_id: str
    body: str
    files_referenced: tuple[PurePosixPath, ...] = field(default_factory=tuple)


# --- internal helpers ------------------------------------------------------


def _feature_root(repo_root: Path, feature_id: str) -> Path:
    """Return ``<repo_root>/.forge/features/<feature_id>/``."""
    return repo_root / ".forge" / "features" / feature_id


def _read_required(path: Path, label: str, feature_id: str) -> str:
    """Read ``path`` or raise ``FileNotFoundError`` naming the feature.

    Plan §P1.4 failure modes: missing SPEC.md and missing PLAN.md
    (target=plan only) raise so the operator sees an explicit signal
    rather than a silently empty prompt body.
    """
    if not path.exists():
        raise FileNotFoundError(f"{label} not found for feature {feature_id!r} at {path}")
    return path.read_text(encoding="utf-8")


def _split_sections(text: str) -> list[tuple[str, str]]:
    """Split Markdown into ``(header_line, body)`` pairs.

    Headers are any line beginning with one or more ``#`` followed by a
    space. The body covers every line until the next header. We use a
    pure ``pathlib`` + line-iteration approach (per discipline rule
    "no regex over feature directory") so the parser cost stays linear
    and the section boundaries are obvious in a debugger.
    """
    sections: list[tuple[str, str]] = []
    current_header: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#") and " " in stripped:
            # Confirm the leading run is only ``#`` characters — guards
            # against ``#tag`` style false positives.
            hashes, _, _rest = stripped.partition(" ")
            if set(hashes) == {"#"}:
                if current_header is not None:
                    sections.append((current_header, "\n".join(buffer).rstrip()))
                current_header = line
                buffer = []
                continue
        if current_header is not None:
            buffer.append(line)
    if current_header is not None:
        sections.append((current_header, "\n".join(buffer).rstrip()))
    return sections


def _normalize_header(header_line: str) -> str:
    """Return the lowercased header text without leading ``#`` or trailing whitespace.

    ``## Acceptance Criteria`` → ``acceptance criteria``. Used for
    tolerant prefix matching (see module docstring).
    """
    return header_line.lstrip("#").strip().lower()


def _find_section(sections: list[tuple[str, str]], prefix: str) -> str | None:
    """Return the body of the first section whose header starts with ``prefix``.

    ``prefix`` is matched case-insensitively against the normalized
    header text. ``None`` when no section matches — caller decides
    whether that is fatal (SPEC sections) or silently skipped
    (UNDERSTANDING / Pre-Mortem).
    """
    needle = prefix.lower()
    for header, body in sections:
        if _normalize_header(header).startswith(needle):
            return body
    return None


def _truncate_words(text: str, cap: int) -> str:
    """Trim ``text`` to the first ``cap`` whitespace-separated words.

    The Intent body is appended verbatim into the prompt; the cap keeps
    long backstory paragraphs from crowding out the slice list / diff.
    Truncation appends an ellipsis token so the reviewer sees the cut
    rather than guessing whether the section ended early.
    """
    words = text.split()
    if len(words) <= cap:
        return text.strip()
    return " ".join(words[:cap]) + " …"


def _extract_files_in_scope(plan_body: str) -> tuple[PurePosixPath, ...]:
    """Pull every file path off ``Files in scope:`` lines in ``plan_body``.

    Order-preserving deduplication so a path mentioned in two slices
    appears once in the returned tuple (matches caller expectation that
    ``files_referenced`` is a set-by-identity overlay for redaction).
    """
    seen: dict[PurePosixPath, None] = {}
    for raw_line in plan_body.splitlines():
        # FORGE PLAN.md slices write the marker as ``**Files in scope:**``;
        # strip Markdown bold runs from both sides so the bare marker is
        # detectable by literal prefix match.
        line = raw_line.strip().strip("*").strip()
        if not line.startswith(_FILES_IN_SCOPE_MARKER):
            continue
        payload = line[len(_FILES_IN_SCOPE_MARKER) :].strip().strip("*").strip()
        for entry in payload.split(","):
            candidate = entry.strip()
            if not candidate:
                continue
            seen[PurePosixPath(candidate)] = None
    return tuple(seen)


def _serialize_articles(articles: list[Article]) -> str:
    """Render kept Constitution articles as a Markdown bullet list.

    Empty list → empty string so the caller's ``"".join`` does not leak
    a stray heading into the prompt body.
    """
    if not articles:
        return ""
    lines = ["", "## Constitution (filtered)", ""]
    for article in articles:
        ref_suffix = f" — {article.reference}" if article.reference else ""
        lines.append(
            f"- **{article.id} [{article.level}] {article.title}**: {article.rule}{ref_suffix}"
        )
    return "\n".join(lines) + "\n"


def _spec_creation_sha(state_payload: dict[str, Any]) -> str | None:
    """Resolve the spec-creation SHA used as the diff's ``A..HEAD`` base.

    Plan step 4: prefer ``state.json["created_at_sha"]`` if present,
    otherwise fall back to the first entry in ``state.commits[]``.
    Returns ``None`` when neither is available so the caller can emit
    the empty-commits annotation rather than ``None..HEAD``.
    """
    explicit = state_payload.get("created_at_sha")
    if isinstance(explicit, str) and explicit:
        return explicit
    commits = state_payload.get("commits")
    if isinstance(commits, list) and commits:
        first = commits[0]
        if isinstance(first, dict):
            sha = first.get("sha")
            if isinstance(sha, str) and sha:
                return sha
    return None


def _git_diff_section(
    repo_root: Path,
    state_payload: dict[str, Any],
) -> str:
    """Render the ``## Diff`` section for target=code prompts.

    Failure modes per plan step 4 + §P1.4 failure-modes block:

    * Empty ``state.commits[]`` → ``_diff unavailable: no commits
      recorded_`` annotation. Not fatal — the reviewer still sees the
      SPEC excerpts and can comment on contract drift.
    * ``git diff`` raises ``subprocess.CalledProcessError`` (e.g. SHA
      not in tree, repo root not a git checkout) → ``_diff unavailable_``
      annotation. Wrapped, never re-raised.
    """
    base_sha = _spec_creation_sha(state_payload)
    if base_sha is None:
        return "## Diff\n\n_diff unavailable: no commits recorded_\n"

    range_spec = f"{base_sha}..HEAD"
    try:
        stat_result = subprocess.run(
            ["git", "diff", "--stat", range_spec],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return "## Diff\n\n_diff unavailable_\n"

    chunks: list[str] = ["## Diff", "", "```", stat_result.stdout.rstrip(), "```", ""]
    seen_files = _collect_commit_files(state_payload)
    chunks.extend(_render_per_file_diffs(repo_root, range_spec, seen_files))
    return "\n".join(chunks)


def _collect_commit_files(state_payload: dict[str, Any]) -> list[str]:
    """Order-preserving dedupe of ``commits[*].files`` string entries.

    Today's state.json schema does not record per-commit file paths;
    when callers populate the field (P2/P3 may do so) this helper
    surfaces them so the per-file diff loop renders one section per
    file instead of falling back to the full-range diff.
    """
    seen: dict[str, None] = {}
    commits = state_payload.get("commits", [])
    if not isinstance(commits, list):
        return []
    for entry in commits:
        if not isinstance(entry, dict):
            continue
        files = entry.get("files")
        if not isinstance(files, list):
            continue
        for file_entry in files:
            if isinstance(file_entry, str) and file_entry:
                seen[file_entry] = None
    return list(seen)


def _render_per_file_diffs(
    repo_root: Path,
    range_spec: str,
    seen_files: list[str],
) -> list[str]:
    """Render per-file diff sections (or a single full-range diff fallback).

    When ``seen_files`` is non-empty we shell out once per file so the
    reviewer sees one ``### path`` heading per change. When empty, we
    issue a single full-range ``git diff`` so the diff still reaches the
    reviewer even though per-commit file lists are not recorded.
    """
    if seen_files:
        out: list[str] = []
        for file_path in sorted(seen_files):
            try:
                per_file = subprocess.run(
                    ["git", "diff", range_spec, "--", file_path],
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    check=True,
                )
            except subprocess.CalledProcessError:
                continue
            out.extend([f"### {file_path}", "", "```diff", per_file.stdout.rstrip(), "```", ""])
        return out

    try:
        per_file = subprocess.run(
            ["git", "diff", range_spec],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError:
        return ["_per-file diff unavailable_\n"]
    return ["### Full diff", "", "```diff", per_file.stdout.rstrip(), "```", ""]


def _commit_files_referenced(state_payload: dict[str, Any]) -> tuple[PurePosixPath, ...]:
    """Order-preserving dedupe of every file path mentioned in commits."""
    seen: dict[PurePosixPath, None] = {}
    commits = state_payload.get("commits", [])
    if not isinstance(commits, list):
        return ()
    for entry in commits:
        if not isinstance(entry, dict):
            continue
        files = entry.get("files")
        if not isinstance(files, list):
            continue
        for file_entry in files:
            if isinstance(file_entry, str) and file_entry:
                seen[PurePosixPath(file_entry)] = None
    return tuple(seen)


# --- public API -----------------------------------------------------------


def build_prompt(
    target: PromptTarget,
    feature_id: str,
    repo_root: Path,
) -> Prompt:
    """Build a self-contained reviewer prompt for ``target``.

    No redaction is performed — the dispatcher calls
    ``tools.redaction.filter()`` on the returned body before any
    external dispatch (see module docstring).

    Args:
        target: ``PromptTarget.plan`` (PLAN.md review) or
            ``PromptTarget.code`` (diff review).
        feature_id: Folder name under ``.forge/features/``.
        repo_root: Repository root containing ``.forge/``.

    Returns:
        ``Prompt`` carrying the Markdown body and the union of file
        paths referenced.

    Raises:
        FileNotFoundError: SPEC.md missing, or PLAN.md missing when
            ``target=plan``. The feature_id appears in the message.
    """
    feature_root = _feature_root(repo_root, feature_id)

    spec_text = _read_required(feature_root / "SPEC.md", "SPEC.md", feature_id)
    spec_sections = _split_sections(spec_text)

    intent_raw = _find_section(spec_sections, "intent") or ""
    intent_body = _truncate_words(intent_raw, _INTENT_WORD_CAP)
    acceptance_body = _find_section(spec_sections, "acceptance") or ""
    negative_body = _find_section(spec_sections, "negative requirements") or ""

    chunks: list[str] = [f"# Reviewer Prompt — {target.value} target — {feature_id}", ""]
    chunks.extend(["# Intent", "", intent_body, ""])
    chunks.extend(["# Acceptance", "", acceptance_body, ""])
    chunks.extend(["# Negative Requirements", "", negative_body, ""])

    understanding_path = feature_root / "UNDERSTANDING.md"
    if understanding_path.exists():
        und_sections = _split_sections(understanding_path.read_text(encoding="utf-8"))
        pre_mortem = _find_section(und_sections, "pre-mortem")
        if pre_mortem is not None:
            chunks.extend(["# Pre-Mortem", "", pre_mortem, ""])

    files_referenced: tuple[PurePosixPath, ...]

    if target is PromptTarget.plan:
        plan_text = _read_required(feature_root / "PLAN.md", "PLAN.md", feature_id)
        chunks.extend(["# Plan", "", plan_text.rstrip(), ""])
        files_referenced = _extract_files_in_scope(plan_text)
    else:
        # target=code — read state.json for commits + render diff.
        state_payload = read_state(feature_root / "state.json")
        chunks.append(_git_diff_section(repo_root, state_payload))
        files_referenced = _commit_files_referenced(state_payload)

    # Constitution articles (P3 helper). The filter reads
    # .forge/CONSTITUTION.md, scopes by intent + files, and respects
    # the 1500-token cap. Empty result → empty string, no leak.
    kept_articles, _dropped = load_and_filter(
        repo_root,
        idea_text=intent_body,
        files_in_scope=_iter_constitution_files(files_referenced),
    )
    constitution_block = _serialize_articles(kept_articles)
    if constitution_block:
        chunks.append(constitution_block)

    chunks.append(_REVIEWER_MANDATE)

    body = "\n".join(chunks)
    return Prompt(
        target=target,
        feature_id=feature_id,
        body=body,
        files_referenced=files_referenced,
    )


def _iter_constitution_files(files: Iterable[PurePosixPath]) -> list[Path]:
    """Adapt ``PurePosixPath`` to the ``Path`` shape ``load_and_filter`` expects.

    ``load_and_filter`` only stringifies the entries to feed the
    tokenizer, so pure → concrete is a safe widening; we keep the API
    boundary explicit so future readers see why the conversion happens
    here rather than at every call site.
    """
    return [Path(str(p)) for p in files]

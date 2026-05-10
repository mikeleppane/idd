"""Tests for ``tools.cross_ai.manual`` — manual-mode orchestration helpers.

Cases (a)-(l) per the cross-ai substrate plan:

* (a)-(d) cover ``write_prompt_to_disk``: target path, parent-dir creation,
  filename timestamp shape (colons replaced with hyphens; deterministic
  via injected ``now=``), and the absolute-path return contract.
* (e)-(f) cover ``read_paste_response``: verbatim read, ``FileNotFoundError``
  with the offending path included in the message.
* (g)-(k) cover ``merge_findings_into_review``: empty-tuple no-op,
  missing-file ``FileNotFoundError``, append-after-existing-rows
  semantics, pipe-character escaping, and the row-count return value.
* (l) snapshots the ``format_disclosure_summary`` output verbatim so any
  drift in the operator-facing preview surfaces as a test failure.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import pytest

from tools.cross_ai.detect import CLI
from tools.cross_ai.disclosure import Disclosure
from tools.cross_ai.manual import (
    format_disclosure_summary,
    merge_findings_into_review,
    read_paste_response,
    write_prompt_to_disk,
)
from tools.cross_ai.parse import Finding
from tools.cross_ai.prompt import Prompt, PromptTarget

# --- shared fixtures -------------------------------------------------------


def _make_prompt(body: str = "# Reviewer Prompt — plan target — feat-x\n\nbody") -> Prompt:
    """Build a minimal ``Prompt`` for write tests; body is what hits disk."""
    return Prompt(
        target=PromptTarget.plan,
        feature_id="feat-x",
        body=body,
        files_referenced=(PurePosixPath("tools/foo.py"),),
    )


def _seed_review_file(repo_root: Path, feature_id: str, target: PromptTarget) -> Path:
    """Drop a REVIEW.<target>.md with one existing data row in place.

    Mirrors the template at ``templates/feature/REVIEW.md`` so the merge
    helper has the same heading + table shape it would see in real use.
    """
    review_dir = repo_root / ".forge" / "features" / feature_id
    review_dir.mkdir(parents=True, exist_ok=True)
    review_path = review_dir / f"REVIEW.{target.value}.md"
    review_path.write_text(
        "---\n"
        "spec: feat-x\n"
        "target: plan\n"
        "status: open\n"
        "cycles: 1\n"
        "---\n"
        "\n"
        "# Findings\n"
        "\n"
        "| ID | Severity | Status | Location | Problem | Recommended Fix | Source |\n"
        "|----|----------|--------|----------|---------|-----------------|--------|\n"
        "| F-1 | BLOCK | open | path/file.py:42 | seed | seed-fix | self |\n"
        "\n"
        "# Decision\n"
        "\n"
        "<resolved>\n",
        encoding="utf-8",
    )
    return review_path


# --- write_prompt_to_disk --------------------------------------------------


def test_write_prompt_to_disk_writes_body_at_expected_path(tmp_path: Path) -> None:
    # (a) Body lands verbatim at ``.forge/features/<id>/cross-ai/<target>-<ts>-prompt.md``.
    prompt = _make_prompt(body="hello reviewer")
    fixed_now = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)

    written = write_prompt_to_disk(prompt, "feat-x", tmp_path, now=fixed_now)

    assert written.read_text(encoding="utf-8") == "hello reviewer"


def test_write_prompt_to_disk_creates_missing_parent_directory(tmp_path: Path) -> None:
    # (b) Parent dir is built on first call; helper does not assume the
    # caller pre-created the cross-ai subtree.
    prompt = _make_prompt()
    target_dir = tmp_path / ".forge" / "features" / "feat-x" / "cross-ai"
    assert not target_dir.exists()

    write_prompt_to_disk(prompt, "feat-x", tmp_path, now=datetime(2026, 1, 1, tzinfo=UTC))

    assert target_dir.is_dir()


def test_write_prompt_to_disk_filename_uses_dash_separated_timestamp(tmp_path: Path) -> None:
    # (c) Filename timestamp is ``YYYY-MM-DDTHH-MM-SSZ`` — colons replaced
    # with hyphens so the path is portable across filesystems. ``now=``
    # injection makes the assertion deterministic.
    prompt = _make_prompt()
    fixed_now = datetime(2026, 5, 10, 14, 23, 7, tzinfo=UTC)

    written = write_prompt_to_disk(prompt, "feat-x", tmp_path, now=fixed_now)

    assert written.name == "plan-2026-05-10T14-23-07Z-prompt.md"


def test_write_prompt_to_disk_returns_absolute_path(tmp_path: Path) -> None:
    # (d) Return value is absolute so callers can persist it without
    # re-resolving against the cwd.
    prompt = _make_prompt()

    written = write_prompt_to_disk(prompt, "feat-x", tmp_path, now=datetime(2026, 1, 1, tzinfo=UTC))

    assert written.is_absolute()


# --- read_paste_response ---------------------------------------------------


def test_read_paste_response_returns_file_contents_verbatim(tmp_path: Path) -> None:
    # (e) Helper is a thin UTF-8 read — no normalization, no trimming.
    target = tmp_path / "response.md"
    payload = "# Findings\n\n| ID | ... |\n"
    target.write_text(payload, encoding="utf-8")

    assert read_paste_response(target) == payload


def test_read_paste_response_missing_file_raises_with_path_in_message(tmp_path: Path) -> None:
    # (f) Missing file surfaces as ``FileNotFoundError`` (propagated from
    # ``Path.read_text``); the path is part of the standard message so
    # operators see which file was expected.
    missing = tmp_path / "absent.md"

    with pytest.raises(FileNotFoundError) as excinfo:
        read_paste_response(missing)

    assert str(missing) in str(excinfo.value)


# --- merge_findings_into_review --------------------------------------------


def test_merge_findings_into_review_empty_tuple_returns_zero_no_write(tmp_path: Path) -> None:
    # (g) Empty tuple is a fast-path no-op: the file must not be touched
    # so a redundant merge cannot accidentally rewrite the table.
    review_path = _seed_review_file(tmp_path, "feat-x", PromptTarget.plan)
    before = review_path.read_text(encoding="utf-8")

    appended = merge_findings_into_review((), PromptTarget.plan, "feat-x", tmp_path)

    assert appended == 0
    assert review_path.read_text(encoding="utf-8") == before


def test_merge_findings_into_review_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    # (h) Missing REVIEW.<target>.md is fatal — caller must run the
    # review skill first to copy the template.
    finding = Finding(
        id="F-2",
        severity="HIGH",
        status="open",
        location="tools/foo.py:1",
        problem="missing test",
        fix="add coverage",
        source="external-codex",
    )

    with pytest.raises(FileNotFoundError) as excinfo:
        merge_findings_into_review((finding,), PromptTarget.plan, "feat-x", tmp_path)

    expected = tmp_path / ".forge" / "features" / "feat-x" / "REVIEW.plan.md"
    assert str(expected) in str(excinfo.value)


def test_merge_findings_into_review_appends_after_existing_data_rows(tmp_path: Path) -> None:
    # (i) Single finding lands on its own line directly after the seeded
    # data row; frontmatter and pre-existing rows are unchanged.
    review_path = _seed_review_file(tmp_path, "feat-x", PromptTarget.plan)
    finding = Finding(
        id="F-2",
        severity="HIGH",
        status="open",
        location="tools/foo.py:1",
        problem="missing test",
        fix="add coverage",
        source="external-codex",
    )

    appended = merge_findings_into_review((finding,), PromptTarget.plan, "feat-x", tmp_path)
    body = review_path.read_text(encoding="utf-8")

    expected_row = (
        "| F-2 | HIGH | open | tools/foo.py:1 | missing test | add coverage | external-codex |"
    )
    assert appended == 1
    assert "| F-1 | BLOCK | open | path/file.py:42 | seed | seed-fix | self |" in body
    assert expected_row in body
    seed_index = body.index("| F-1 |")
    new_index = body.index("| F-2 |")
    assert seed_index < new_index
    # Frontmatter survived the merge unaltered.
    assert body.startswith("---\nspec: feat-x\n")
    # Trailing decision section survived too — append did not truncate.
    assert "# Decision" in body


def test_merge_findings_into_review_escapes_pipe_characters_in_fields(tmp_path: Path) -> None:
    # (j) A literal ``|`` inside any field would split the cell; the
    # helper escapes it as ``\|`` so the table stays well-formed.
    _seed_review_file(tmp_path, "feat-x", PromptTarget.plan)
    finding = Finding(
        id="F-3",
        severity="MEDIUM",
        status="open",
        location="tools/bar.py:9",
        problem="a | b",
        fix="rewrite",
        source="external-codex",
    )

    merge_findings_into_review((finding,), PromptTarget.plan, "feat-x", tmp_path)
    body = (tmp_path / ".forge" / "features" / "feat-x" / "REVIEW.plan.md").read_text(
        encoding="utf-8"
    )

    assert r"a \| b" in body
    assert "a | b |" not in body.split("\n")[-5:][-3]  # the new row's Problem cell is escaped


def test_merge_findings_into_review_returns_count_of_rows_appended(tmp_path: Path) -> None:
    # (k) Return value matches ``len(findings)`` — no silent dedupe.
    _seed_review_file(tmp_path, "feat-x", PromptTarget.plan)
    findings = (
        Finding(
            id="F-4",
            severity="HIGH",
            status="open",
            location="loc-a",
            problem="p-a",
            fix="f-a",
            source="external-codex",
        ),
        Finding(
            id="F-5",
            severity="LOW",
            status="open",
            location="loc-b",
            problem="p-b",
            fix="f-b",
            source="external-codex",
        ),
        Finding(
            id="F-6",
            severity="INFO",
            status="open",
            location="loc-c",
            problem="p-c",
            fix="f-c",
            source="external-codex",
        ),
    )

    appended = merge_findings_into_review(findings, PromptTarget.plan, "feat-x", tmp_path)

    assert appended == len(findings) == 3


# --- format_disclosure_summary ---------------------------------------------


def test_format_disclosure_summary_matches_expected_snapshot(tmp_path: Path) -> None:
    # (l) Operator-facing snapshot is the dispatcher's primary surface;
    # any drift here is user-visible, so the assertion is verbatim.
    disclosure = Disclosure(
        target=PromptTarget.code,
        cli=CLI.codex,
        file_list=(PurePosixPath("tools/foo.py"), PurePosixPath("tools/bar.py")),
        excluded_files=(PurePosixPath(".env"),),
        diff_loc=42,
        command_preview="codex <self-contained-prompt>",
        prompt_tokens=1234,
        prompt_cost_usd=0.0123,
        had_redactions=True,
        cost_warn_triggered=True,
    )
    prompt_path = tmp_path / "prompt.md"

    rendered = format_disclosure_summary(disclosure, prompt_path)

    expected = (
        "Cross-AI review (manual mode) — review before sending\n"
        "  Target:           code\n"
        "  Reviewer CLI:     codex\n"
        "  Files referenced: 2\n"
        "  Files excluded:   1 (redaction)\n"
        "  Diff LOC:         42\n"
        "  Prompt tokens:    1234 (estimate)\n"
        "  Estimated cost:   $0.0123 (advisory)\n"
        "  Cost warn:        yes\n"
        "  Redactions:       yes\n"
        "  Command preview:  codex <self-contained-prompt>\n"
        f"  Prompt path:      {prompt_path}\n"
        "\n"
        "  Run externally:\n"
        f"    codex < {prompt_path} > response.md\n"
        "\n"
        "  Then paste back:\n"
        "    /forge:review --cross-ai-paste response.md"
    )
    assert rendered == expected


def test_format_disclosure_summary_honours_paste_back_command_override(tmp_path: Path) -> None:
    # (l-bis) Custom ``paste_back_command`` replaces the default literal so
    # callers wrapping the helper can advertise a specialized command.
    disclosure = Disclosure(
        target=PromptTarget.plan,
        cli=CLI.claude,
        file_list=(),
        excluded_files=(),
        diff_loc=0,
        command_preview="claude <self-contained-prompt>",
        prompt_tokens=10,
        prompt_cost_usd=0.0,
        had_redactions=False,
        cost_warn_triggered=False,
    )
    prompt_path = tmp_path / "p.md"

    rendered = format_disclosure_summary(
        disclosure, prompt_path, paste_back_command="/custom paste"
    )

    assert "    /custom paste" in rendered
    # And the default literal does not leak in when overridden.
    assert "/forge:review --cross-ai-paste response.md" not in rendered
    # Flag rendering for the false branches is "no", not omitted.
    assert "  Cost warn:        no" in rendered
    assert "  Redactions:       no" in rendered

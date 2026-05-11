"""Tests for ``tools.cross_ai.prompt`` — per-target reviewer prompt builder.

Cases (a)-(g) per the cross-ai substrate plan:
  * (a) target=plan with full feature folder → body carries Acceptance,
    Pre-Mortem, PLAN.md verbatim, and the reviewer mandate.
  * (b) target=plan without UNDERSTANDING.md → Pre-Mortem section omitted
    silently (no crash, no placeholder leak).
  * (c) target=code with one execute commit → diff stat plus per-file
    diff appears in body.
  * (d) target=code with empty ``state.commits[]`` → body shows the
    ``_diff unavailable: no commits recorded_`` annotation rather than
    raising.
  * (e) ``Prompt.files_referenced`` for target=plan extracts paths from
    every ``Files in scope:`` slice header.
  * (f) ``Prompt`` is a frozen dataclass — assignment raises.
  * (g) Missing SPEC.md → ``FileNotFoundError`` whose message names the
    feature_id (no silent default body).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path, PurePosixPath

import pytest

from tools.cross_ai.prompt import Prompt, PromptTarget, build_prompt

# --- helpers ----------------------------------------------------------------

_SPEC_BODY = """\
# Sample Feature

## Intent
Wire a kill switch into the payments path so operators can disable
charges from the runtime config without a redeploy.

## Acceptance Criteria
- Flag off → 503 returned within 50ms.
- Flag on → existing 200 path unchanged.

## Negative Requirements
- MUST NOT cache flag value across requests.
- MUST NOT log the flag holder identity.

## Open Questions
- (none)
"""

_PLAN_BODY = """\
# Verified Dependencies
- requests ≥ 2.31

# Slice 1: flag store + read API
**Files in scope:** src/feature_flag.py, src/flag_store.py, tests/test_feature_flag.py

# Slice 2: HTTP 503 / 200
**Files in scope:** src/payments.py, tests/test_payments.py
"""

_UND_BODY = """\
# Confirmed Assumptions
- Flag store is single-writer.

# Pre-Mortem (Top Failure Modes)
- Flag stuck on after redeploy → outage.
- Cache TTL hides flag flip → stale 200s.

# Shared Model Statement
- We model the flag as monotonic per request.
"""

_STATE_PAYLOAD: dict[str, object] = {
    "feature_id": "2026-05-10-sample",
    "tier": "standard",
    "current_phase": "execute",
    "phases": {"execute": {"status": "in_progress"}},
    "skipped": [],
    "deviations": [],
    "commits": [
        {"sha": "abc1234", "phase": "spec", "subject": "feat(spec): seed sample feature"},
        {"sha": "def5678", "phase": "execute", "subject": "feat(payments): wire kill switch"},
    ],
}


def _seed_feature(
    repo_root: Path,
    feature_id: str,
    *,
    spec: str | None = _SPEC_BODY,
    plan: str | None = _PLAN_BODY,
    understanding: str | None = _UND_BODY,
    state: dict[str, object] | None = None,
) -> Path:
    folder = repo_root / ".forge" / "features" / feature_id
    folder.mkdir(parents=True, exist_ok=True)
    if spec is not None:
        (folder / "SPEC.md").write_text(spec, encoding="utf-8")
    if plan is not None:
        (folder / "PLAN.md").write_text(plan, encoding="utf-8")
    if understanding is not None:
        (folder / "UNDERSTANDING.md").write_text(understanding, encoding="utf-8")
    if state is not None:
        (folder / "state.json").write_text(json.dumps(state) + "\n", encoding="utf-8")
    return folder


# --- (a) target=plan happy path --------------------------------------------


def test_plan_target_includes_acceptance_premortem_plan_body_and_mandate(
    tmp_path: Path,
) -> None:
    feature_id = "2026-05-10-sample"
    _seed_feature(tmp_path, feature_id)

    prompt = build_prompt(PromptTarget.plan, feature_id, tmp_path)

    assert prompt.target is PromptTarget.plan
    assert prompt.feature_id == feature_id
    # Section presence — Markdown headers with the canonical names.
    assert "# Acceptance" in prompt.body
    assert "# Negative Requirements" in prompt.body
    assert "# Intent" in prompt.body
    assert "# Pre-Mortem" in prompt.body
    # PLAN.md is reproduced verbatim (slice header survives).
    assert "Slice 1: flag store + read API" in prompt.body
    # Reviewer mandate footer is the contracted Markdown table spec.
    assert "ID | Severity | Status | Location | Problem | Fix | Source" in prompt.body
    assert "BLOCK / HIGH / MEDIUM / LOW / INFO" in prompt.body
    assert "[constitution:A<n>]" in prompt.body


def test_reviewer_mandate_routes_constitution_tags_to_problem_column(
    tmp_path: Path,
) -> None:
    """Coupling test: the prompt mandate MUST direct reviewers to the
    Problem column for constitution tags. The parser overwrites the
    Source column to ``external-<reviewer>`` and only preserves tags in
    Problem (see ``tools/cross_ai/parse.py:19``). A drift here silently
    drops the constitution-routing signal end-to-end.
    """
    feature_id = "2026-05-10-sample"
    _seed_feature(tmp_path, feature_id)

    prompt = build_prompt(PromptTarget.plan, feature_id, tmp_path)

    assert "Problem column" in prompt.body
    assert "Source column" in prompt.body  # mentioned to warn it's overwritten
    # Negative guard: the mandate must NOT tell reviewers to put tags in
    # the Source column — that path silently drops the routing tag.
    assert "[constitution:A<n>]` in the\n  Source column" not in prompt.body


# --- (b) target=plan without UNDERSTANDING.md ------------------------------


def test_plan_target_omits_premortem_when_understanding_missing(
    tmp_path: Path,
) -> None:
    feature_id = "2026-05-10-no-und"
    _seed_feature(tmp_path, feature_id, understanding=None)

    prompt = build_prompt(PromptTarget.plan, feature_id, tmp_path)

    assert "# Pre-Mortem" not in prompt.body
    # Other sections still present — proves the missing file did not abort
    # the build.
    assert "# Acceptance" in prompt.body
    assert "Slice 1:" in prompt.body


# --- (c) target=code with one execute commit -------------------------------


def test_code_target_includes_diff_stat_and_per_file_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feature_id = "2026-05-10-sample"
    _seed_feature(tmp_path, feature_id, state=_STATE_PAYLOAD)

    fake_stat = (
        " src/payments.py | 12 ++++++++----\n 1 file changed, 8 insertions(+), 4 deletions(-)\n"
    )
    fake_name_only = "src/payments.py\n"
    fake_per_file = "diff --git a/src/payments.py b/src/payments.py\n@@ -1 +1,5 @@\n+kill switch\n"

    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if "--name-only" in args:
            return subprocess.CompletedProcess(args, 0, stdout=fake_name_only, stderr="")
        if "--stat" in args:
            return subprocess.CompletedProcess(args, 0, stdout=fake_stat, stderr="")
        return subprocess.CompletedProcess(args, 0, stdout=fake_per_file, stderr="")

    monkeypatch.setattr("tools.cross_ai.prompt.subprocess.run", fake_run)

    prompt = build_prompt(PromptTarget.code, feature_id, tmp_path)

    assert prompt.target is PromptTarget.code
    assert "src/payments.py | 12" in prompt.body
    assert "kill switch" in prompt.body
    # Spec creation SHA = first commit in state.commits[]; HEAD reference
    # used via "..HEAD" range.
    assert any("abc1234..HEAD" in arg for call in calls for arg in call)
    # files_referenced is sourced from `git diff --name-only`, not from the
    # state.json schema (which forbids commits[*].files via
    # additionalProperties: false). The redaction overlay must therefore
    # carry exactly what git reports as changed.
    assert prompt.files_referenced == (PurePosixPath("src/payments.py"),)
    assert any("--name-only" in arg for call in calls for arg in call)


# --- (d) target=code with empty state.commits[] ----------------------------


def test_code_target_empty_commits_emits_unavailable_annotation(
    tmp_path: Path,
) -> None:
    feature_id = "2026-05-10-empty"
    empty_state = {**_STATE_PAYLOAD, "feature_id": feature_id, "commits": []}
    _seed_feature(tmp_path, feature_id, state=empty_state)

    prompt = build_prompt(PromptTarget.code, feature_id, tmp_path)

    assert "_diff unavailable: no commits recorded_" in prompt.body
    # Acceptance section still present — proves the spec read still ran.
    assert "# Acceptance" in prompt.body


# --- (e) files_referenced for target=plan ----------------------------------


def test_plan_target_extracts_files_referenced_from_slice_headers(
    tmp_path: Path,
) -> None:
    feature_id = "2026-05-10-sample"
    _seed_feature(tmp_path, feature_id)

    prompt = build_prompt(PromptTarget.plan, feature_id, tmp_path)

    assert prompt.files_referenced == (
        PurePosixPath("src/feature_flag.py"),
        PurePosixPath("src/flag_store.py"),
        PurePosixPath("tests/test_feature_flag.py"),
        PurePosixPath("src/payments.py"),
        PurePosixPath("tests/test_payments.py"),
    )


# --- (f) Prompt is frozen --------------------------------------------------


def test_prompt_dataclass_is_frozen() -> None:
    prompt = Prompt(
        target=PromptTarget.plan,
        feature_id="2026-05-10-sample",
        body="x",
        files_referenced=(),
    )
    with pytest.raises(FrozenInstanceError):
        prompt.body = "mutated"  # type: ignore[misc]


# --- (g) Missing SPEC.md ---------------------------------------------------


def test_missing_spec_raises_filenotfounderror_with_feature_id(
    tmp_path: Path,
) -> None:
    feature_id = "2026-05-10-nospec"
    # Folder exists but SPEC.md absent.
    (tmp_path / ".forge" / "features" / feature_id).mkdir(parents=True)

    with pytest.raises(FileNotFoundError) as excinfo:
        build_prompt(PromptTarget.plan, feature_id, tmp_path)
    assert feature_id in str(excinfo.value)


# --- bonus: target=code git diff failure degrades to _diff unavailable_ ----


def test_code_target_git_diff_failure_degrades_to_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    feature_id = "2026-05-10-sample"
    _seed_feature(tmp_path, feature_id, state=_STATE_PAYLOAD)

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(returncode=128, cmd=args, stderr="bad sha")

    monkeypatch.setattr("tools.cross_ai.prompt.subprocess.run", fake_run)

    prompt = build_prompt(PromptTarget.code, feature_id, tmp_path)

    assert "_diff unavailable_" in prompt.body
    # When git fails entirely, the file inventory is empty — the redaction
    # overlay must not carry phantom paths the diff did not surface.
    assert prompt.files_referenced == ()


def test_code_target_per_file_diff_failure_skips_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One file fails its per-file diff while another succeeds — the
    failing file's section is silently dropped and the rest of the diff
    block still renders. Exercises the ``continue`` branch in
    ``_render_per_file_diffs``.
    """
    feature_id = "2026-05-10-sample"
    _seed_feature(tmp_path, feature_id, state=_STATE_PAYLOAD)

    fake_stat = " a.py | 1\n b.py | 1\n 2 files changed\n"
    fake_name_only = "a.py\nb.py\n"

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--name-only" in args:
            return subprocess.CompletedProcess(args, 0, stdout=fake_name_only, stderr="")
        if "--stat" in args:
            return subprocess.CompletedProcess(args, 0, stdout=fake_stat, stderr="")
        # Per-file diff: succeed for a.py, fail for b.py.
        if "--" in args and args[args.index("--") + 1] == "a.py":
            return subprocess.CompletedProcess(args, 0, stdout="diff a.py", stderr="")
        raise subprocess.CalledProcessError(returncode=128, cmd=args, stderr="ENOSHA")

    monkeypatch.setattr("tools.cross_ai.prompt.subprocess.run", fake_run)

    prompt = build_prompt(PromptTarget.code, feature_id, tmp_path)

    assert "### a.py" in prompt.body
    assert "### b.py" not in prompt.body  # silently dropped
    assert prompt.files_referenced == (PurePosixPath("a.py"), PurePosixPath("b.py"))


def test_code_target_name_only_empty_falls_through_to_full_diff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When ``git diff --name-only`` reports no changes (empty stdout)
    but ``--stat`` succeeds, ``_render_per_file_diffs`` falls through
    to the full-range diff fallback so the reviewer still sees the
    change-set body. Exercises the empty-list branch.
    """
    feature_id = "2026-05-10-sample"
    _seed_feature(tmp_path, feature_id, state=_STATE_PAYLOAD)

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--name-only" in args:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if "--stat" in args:
            return subprocess.CompletedProcess(args, 0, stdout=" no files\n", stderr="")
        return subprocess.CompletedProcess(args, 0, stdout="raw diff body", stderr="")

    monkeypatch.setattr("tools.cross_ai.prompt.subprocess.run", fake_run)

    prompt = build_prompt(PromptTarget.code, feature_id, tmp_path)

    assert "### Full diff" in prompt.body
    assert "raw diff body" in prompt.body
    assert prompt.files_referenced == ()


def test_code_target_full_diff_failure_emits_per_file_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``--name-only`` reports nothing AND the full-range diff fails →
    ``_per-file diff unavailable_`` annotation. Exercises the
    ``CalledProcessError`` arm of the empty-list branch.
    """
    feature_id = "2026-05-10-sample"
    _seed_feature(tmp_path, feature_id, state=_STATE_PAYLOAD)

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "--name-only" in args:
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        if "--stat" in args:
            return subprocess.CompletedProcess(args, 0, stdout=" stat ok\n", stderr="")
        raise subprocess.CalledProcessError(returncode=128, cmd=args, stderr="boom")

    monkeypatch.setattr("tools.cross_ai.prompt.subprocess.run", fake_run)

    prompt = build_prompt(PromptTarget.code, feature_id, tmp_path)

    assert "_per-file diff unavailable_" in prompt.body


def test_code_target_uses_explicit_created_at_sha(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``state.created_at_sha`` wins over the first commit when present.
    Exercises the explicit-SHA branch of ``_spec_creation_sha``.
    """
    feature_id = "2026-05-10-sample"
    state_with_explicit = {**_STATE_PAYLOAD, "created_at_sha": "deadbee"}
    _seed_feature(tmp_path, feature_id, state=state_with_explicit)

    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr("tools.cross_ai.prompt.subprocess.run", fake_run)

    build_prompt(PromptTarget.code, feature_id, tmp_path)

    assert any("deadbee..HEAD" in arg for call in calls for arg in call)


def test_intent_truncated_to_word_cap_with_ellipsis(tmp_path: Path) -> None:
    """Long intents are clipped to the documented 200-word cap and the
    cut is signaled with a trailing ellipsis token so the reviewer can
    tell the section was trimmed.
    """
    feature_id = "2026-05-10-long-intent"
    long_intent = "word " * 300  # 300 whitespace-separated tokens
    spec_with_long_intent = (
        "# Sample\n\n## Intent\n"
        + long_intent
        + "\n\n## Acceptance Criteria\n- ok.\n\n## Negative Requirements\n- none.\n"
    )
    _seed_feature(tmp_path, feature_id, spec=spec_with_long_intent, understanding=None)

    prompt = build_prompt(PromptTarget.plan, feature_id, tmp_path)

    # Trimmed body ends with the ellipsis token; the full 300-word run is
    # not present.
    assert "…" in prompt.body
    intent_segment_end = prompt.body.find("# Acceptance")
    intent_segment = prompt.body[:intent_segment_end]
    assert intent_segment.count("word") <= 210  # cap + small slack for surrounding text


def test_constitution_articles_are_serialized_when_present(tmp_path: Path) -> None:
    """When ``.forge/CONSTITUTION.md`` is present and at least one
    article passes the scope filter, the prompt body includes the
    rendered article block. Exercises ``_serialize_articles``.
    """
    feature_id = "2026-05-10-sample"
    _seed_feature(tmp_path, feature_id)
    constitution = (
        '---\nversion: 0.1.0\ncreated: "2026-05-10"\n---\n\n'
        "# Constitution\n\n"
        "## Article 1 — Feature flag cache discipline [CRITICAL]\n\n"
        "**Rule:** Feature flag values MUST NOT be cached across requests "
        "in the payments path.\n"
        "**Reference:** ADR-2026-05-feature-flag-cache\n"
        "**Rationale:** Cached flags hide flips and produce stale 200s.\n"
        "**Exception:** None.\n"
    )
    (tmp_path / ".forge").mkdir(exist_ok=True)
    (tmp_path / ".forge" / "CONSTITUTION.md").write_text(constitution, encoding="utf-8")

    prompt = build_prompt(PromptTarget.plan, feature_id, tmp_path)

    assert "## Constitution (filtered)" in prompt.body
    assert "A1" in prompt.body

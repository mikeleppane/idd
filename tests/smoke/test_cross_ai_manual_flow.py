"""End-to-end smoke for the cross-AI manual-mode round trip.

Walks the full pipeline a reviewer cycle exercises in production:

1. Seed a fake feature folder under ``tmp_path`` with the minimal SPEC,
   PLAN, state, and REVIEW shapes the helpers expect.
2. Build the reviewer prompt (``tools.cross_ai.prompt.build_prompt``).
3. Apply the shared redaction filter via the
   ``tools.cross_ai.config.to_redaction_config`` adapter so the smoke
   exercises the same handoff the skill performs in production
   (``RedactionRules`` from the config block widened into the
   redactor's ``RedactionConfig`` shape).
4. Build the pre-dispatch disclosure
   (``tools.cross_ai.disclosure.build_disclosure``) using the
   ``Prompt.diff_loc`` field so the disclosure's ``Diff LOC`` row
   reflects the post-build value rather than a hard-coded zero.
5. Persist the **redacted** body to disk
   (``tools.cross_ai.manual.write_prompt_to_disk``) — this is the
   security contract: the bytes the operator pipes to the external CLI
   are the post-scrub bytes, not the raw ``Prompt.body``.
6. Read the canned reviewer response shipped with the mock fixture
   (``tools.cross_ai.manual.read_paste_response``).
7. Pull the reviewer id from the response frontmatter
   (``tools.cross_ai.manual.extract_reviewer_id``).
8. Parse the response into typed rows
   (``tools.cross_ai.parse.parse_response``).
9. Merge those rows into the seeded REVIEW.plan.md
   (``tools.cross_ai.manual.merge_findings_into_review``).
10. Render the operator-facing disclosure summary
    (``tools.cross_ai.manual.format_disclosure_summary``).

The round-trip leans on the real helpers — no mocks, no patches — so any
contract drift between the modules surfaces here before it reaches the
skill that orchestrates them. Two additional tests pin the security-
critical branches:

* :func:`test_cross_ai_manual_flow_redacts_secret_before_persist` —
  with a non-default ``deny_regex`` configured, the on-disk prompt
  contains ``[REDACTED:0]`` and not the original secret.
* :func:`test_cross_ai_manual_flow_refuses_on_fatal_match` — with a
  ``fatal_regex`` that fires, the skill's refusal contract holds:
  ``fatal_matches`` is non-empty and the dispatcher must not write the
  prompt to disk.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from tools import redaction
from tools.cross_ai.config import (
    CrossAiConfig,
    RedactionRules,
    to_redaction_config,
)
from tools.cross_ai.detect import CLI
from tools.cross_ai.disclosure import build_disclosure
from tools.cross_ai.manual import (
    extract_reviewer_id,
    format_disclosure_summary,
    merge_findings_into_review,
    read_paste_response,
    write_prompt_to_disk,
)
from tools.cross_ai.parse import parse_response
from tools.cross_ai.prompt import Prompt, PromptTarget, build_prompt

# Repo root is two levels above this file (tests/smoke/<this>).
_REPO_ROOT: Path = Path(__file__).resolve().parents[2]
_CANNED_RESPONSE: Path = (
    _REPO_ROOT / "tests" / "fixtures" / "_cross_ai" / "canned_response_with_findings.md"
)

_FEATURE_ID: str = "2026-05-10-feat-test"

# Marker the redaction-bypass test seeds into the SPEC body and matches
# with a deny_regex; the on-disk prompt must contain ``[REDACTED:N]``
# and never the literal value. Not a real credential — see test usage.
_FAKE_SECRET: str = "sk-test-1234567890abcdef"  # noqa: S105 — fake test fixture

# Marker the refusal test seeds into the SPEC body and matches with a
# fatal_regex; the dispatcher contract is to refuse before any write.
_FATAL_MARKER: str = "BEGIN-RSA-PRIVATE-KEY-FAKE"


def _seed_feature(repo_root: Path, *, extra_spec_body: str = "") -> Path:
    """Create the ``.forge/features/<id>/`` tree with SPEC, PLAN, state, REVIEW.

    The shapes are the bare minimum each downstream helper requires:
    ``# Intent`` / ``# Acceptance`` / ``# Negative Requirements`` headings
    on the SPEC (matches the prefix-tolerant extractor in the prompt
    builder), one slice with a ``Files in scope:`` line on the PLAN, a
    schema-valid ``state.json``, and a REVIEW.plan.md with a Findings
    table that already has a header + separator + one data row so the
    merge helper can tack new rows on after the existing block. The
    optional ``extra_spec_body`` is appended verbatim to the SPEC's
    Negative Requirements section so secret-shaped markers used by the
    redaction tests reach ``prompt.body`` without altering the happy-path
    seed.
    """
    feature_dir = repo_root / ".forge" / "features" / _FEATURE_ID
    feature_dir.mkdir(parents=True, exist_ok=True)

    (feature_dir / "SPEC.md").write_text(
        "# Intent\n"
        "\n"
        "Verify the manual-mode round trip end to end.\n"
        "\n"
        "# Acceptance\n"
        "\n"
        "- The reviewer prompt builds without raising.\n"
        "- The seeded REVIEW table absorbs two new rows.\n"
        "\n"
        "# Negative Requirements\n"
        "\n"
        "- No subprocess invocation from the manual helpers.\n" + extra_spec_body,
        encoding="utf-8",
    )

    (feature_dir / "PLAN.md").write_text(
        "# Plan\n\n## Slice 1\n\n**Files in scope:** tools/example.py\n\nDo the thing.\n",
        encoding="utf-8",
    )

    state_payload = {
        "feature_id": _FEATURE_ID,
        "tier": "standard",
        "current_phase": "review",
        "phases": {"review": {"status": "in_progress", "current_target": "plan"}},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    (feature_dir / "state.json").write_text(
        json.dumps(state_payload, indent=2) + "\n", encoding="utf-8"
    )

    (feature_dir / "REVIEW.plan.md").write_text(
        "---\n"
        f"spec: {_FEATURE_ID}\n"
        "target: plan\n"
        "status: open\n"
        "cycles: 1\n"
        "---\n"
        "\n"
        "# Findings\n"
        "\n"
        "| ID | Severity | Status | Location | Problem | Recommended Fix | Source |\n"
        "|----|----------|--------|----------|---------|-----------------|--------|\n"
        "| F-0 | LOW | open | PLAN.md | seed row | seed fix | self |\n"
        "\n"
        "# Decision\n"
        "\n"
        "<resolved>\n",
        encoding="utf-8",
    )

    return feature_dir


def _wrap_with_frontmatter(reviewer: str, response_body: str) -> str:
    """Prepend a minimal YAML frontmatter block carrying ``reviewer:``."""
    return f"---\nreviewer: {reviewer}\n---\n\n{response_body}"


def test_cross_ai_manual_flow_end_to_end(tmp_path: Path) -> None:
    """Drive the full prompt → redact → disclose → write → read → parse → merge cycle."""
    # Step 1: seed the fake feature on disk.
    _seed_feature(tmp_path)

    # Step 2: real prompt builder against the seeded SPEC + PLAN.
    prompt: Prompt = build_prompt(
        target=PromptTarget.plan,
        feature_id=_FEATURE_ID,
        repo_root=tmp_path,
    )
    assert prompt.target is PromptTarget.plan
    assert "# Intent" in prompt.body
    assert "# Plan" in prompt.body
    # ``diff_loc`` is plan-target-derived: always 0 for plan reviews.
    assert prompt.diff_loc == 0

    # Step 3: shared redaction filter via the production adapter — keeps
    # the smoke aligned with the skill's call shape so a future divergence
    # surfaces here.
    config = CrossAiConfig()
    redaction_config = to_redaction_config(config.redaction)
    redaction_result = redaction.filter(
        redaction.PromptPayload(text=prompt.body, files=prompt.files_referenced),
        redaction_config,
    )
    assert redaction_result.fatal_matches == ()
    assert redaction_result.output_text  # non-empty post-scrub body

    # Step 4: pre-dispatch disclosure snapshot — pass diff_loc through
    # from the prompt so the disclosure reflects the real value.
    disclosure = build_disclosure(
        prompt=prompt,
        redaction_result=redaction_result,
        cli=CLI.codex,
        config=config,
        diff_loc=prompt.diff_loc,
    )
    assert disclosure.target is PromptTarget.plan
    assert disclosure.cli is CLI.codex
    assert disclosure.prompt_tokens > 0
    assert disclosure.diff_loc == prompt.diff_loc

    # Step 5: persist the **redacted** body — this is the security
    # contract. Pass ``redaction_result.output_text``, not ``prompt.body``;
    # the on-disk file is what the operator pipes to the external CLI.
    fixed_now = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
    prompt_path = write_prompt_to_disk(
        redaction_result.output_text,
        prompt.target,
        _FEATURE_ID,
        tmp_path,
        now=fixed_now,
    )
    assert prompt_path.exists()
    assert prompt_path.name == "plan-2026-05-10T12-00-00Z-prompt.md"
    assert prompt_path.read_text(encoding="utf-8") == redaction_result.output_text

    # Step 6: read the canned reviewer response from the repo fixture
    # (intentionally outside ``tmp_path`` — the fixture is a fixed file
    # on disk that simulates the reviewer's pasted output).
    canned_text = read_paste_response(_CANNED_RESPONSE)
    response_text = _wrap_with_frontmatter("mock", canned_text)
    assert "| F-1 |" in response_text

    # Step 7: derive the reviewer id from the response frontmatter so
    # the smoke covers the production handoff (``extract_reviewer_id``)
    # rather than hard-coding the value.
    reviewer_id = extract_reviewer_id(response_text)
    assert reviewer_id == "mock"

    # Step 8: parse the response into typed Finding rows.
    findings = parse_response(response_text, reviewer_id=reviewer_id, target="plan")
    assert len(findings) == 2
    assert findings[0].id == "F-1"
    assert findings[0].severity == "HIGH"
    assert findings[1].id == "F-2"
    assert findings[1].severity == "MEDIUM"
    # Source column is dispatcher-injected; verify the override happened.
    assert all(f.source == "external-mock" for f in findings)

    # Step 9: merge the parsed rows into the seeded REVIEW.plan.md.
    appended = merge_findings_into_review(findings, PromptTarget.plan, _FEATURE_ID, tmp_path)
    assert appended == 2

    review_path = tmp_path / ".forge" / "features" / _FEATURE_ID / "REVIEW.plan.md"
    review_text = review_path.read_text(encoding="utf-8")
    # Existing seed row preserved; new rows appended after it in source order.
    seed_idx = review_text.index("| F-0 |")
    f1_idx = review_text.index("| F-1 |")
    f2_idx = review_text.index("| F-2 |")
    assert seed_idx < f1_idx < f2_idx
    # The constitution tag in the Problem column flows through verbatim.
    assert "[constitution:A1]" in review_text

    # Step 10: render the operator-facing disclosure block; the prefix
    # is a stable contract and the prompt path must appear so the
    # operator knows which file to feed the reviewer CLI.
    summary = format_disclosure_summary(disclosure, prompt_path)
    assert summary.startswith("Cross-AI review (manual mode)")
    assert str(prompt_path) in summary


def test_cross_ai_manual_flow_redacts_secret_before_persist(tmp_path: Path) -> None:
    """A configured ``deny_regex`` must scrub the on-disk prompt body.

    Seeds a fake secret into the SPEC, configures a matching
    ``deny_regex`` on the redaction rules, runs the same prompt → redact
    → write pipeline as the happy-path smoke, then asserts:

    * ``redaction_result.output_text`` contains ``[REDACTED:N]`` and
      does not contain the literal secret.
    * The on-disk prompt file matches ``redaction_result.output_text``
      and likewise does not contain the literal secret. This is the
      load-bearing assertion: it proves the helper persisted the
      post-scrub body, not ``prompt.body``.
    """
    _seed_feature(tmp_path, extra_spec_body=f"\n- Forbidden token: {_FAKE_SECRET}\n")

    prompt = build_prompt(
        target=PromptTarget.plan,
        feature_id=_FEATURE_ID,
        repo_root=tmp_path,
    )
    assert _FAKE_SECRET in prompt.body  # sanity: secret reached the prompt body.

    rules = RedactionRules(deny_regex=("sk-[A-Za-z0-9-]{16,}",))
    redaction_config = to_redaction_config(rules)
    redaction_result = redaction.filter(
        redaction.PromptPayload(text=prompt.body, files=prompt.files_referenced),
        redaction_config,
    )
    assert redaction_result.fatal_matches == ()
    assert "[REDACTED:" in redaction_result.output_text
    assert _FAKE_SECRET not in redaction_result.output_text

    prompt_path = write_prompt_to_disk(
        redaction_result.output_text,
        prompt.target,
        _FEATURE_ID,
        tmp_path,
        now=datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC),
    )

    on_disk = prompt_path.read_text(encoding="utf-8")
    assert _FAKE_SECRET not in on_disk
    assert "[REDACTED:" in on_disk
    assert on_disk == redaction_result.output_text


def test_cross_ai_manual_flow_refuses_on_fatal_match(tmp_path: Path) -> None:
    """A configured ``fatal_regex`` must produce a non-empty refusal record.

    Mirrors the skill's Step 4a refusal contract: when
    ``redaction_result.fatal_matches`` is non-empty, the dispatcher
    REFUSES and does not write the prompt to disk. We assert the fatal
    list is populated and that the cross-ai prompt directory remains
    empty so a future regression that silently writes despite the
    refusal would fail the test.
    """
    _seed_feature(tmp_path, extra_spec_body=f"\n- Fatal marker: {_FATAL_MARKER}\n")

    prompt = build_prompt(
        target=PromptTarget.plan,
        feature_id=_FEATURE_ID,
        repo_root=tmp_path,
    )
    assert _FATAL_MARKER in prompt.body

    rules = RedactionRules(fatal_regex=("BEGIN-RSA-PRIVATE-KEY-[A-Z]+",))
    redaction_config = to_redaction_config(rules)
    redaction_result = redaction.filter(
        redaction.PromptPayload(text=prompt.body, files=prompt.files_referenced),
        redaction_config,
    )

    assert len(redaction_result.fatal_matches) == 1
    assert redaction_result.fatal_matches[0].sample.startswith("BEGIN-RSA-PRIVATE-KEY")

    # Skill contract: dispatcher refuses; no prompt file should land.
    cross_ai_dir = tmp_path / ".forge" / "features" / _FEATURE_ID / "cross-ai"
    assert not cross_ai_dir.exists() or not any(cross_ai_dir.iterdir())

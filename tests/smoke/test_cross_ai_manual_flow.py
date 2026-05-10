"""End-to-end smoke for the cross-AI manual-mode round trip.

Walks the full pipeline a reviewer cycle exercises in production:

1. Seed a fake feature folder under ``tmp_path`` with the minimal SPEC,
   PLAN, state, and REVIEW shapes the helpers expect.
2. Build the reviewer prompt (``tools.cross_ai.prompt.build_prompt``).
3. Apply the shared redaction filter (``tools.redaction.filter``).
4. Build the pre-dispatch disclosure
   (``tools.cross_ai.disclosure.build_disclosure``).
5. Persist the prompt to disk
   (``tools.cross_ai.manual.write_prompt_to_disk``).
6. Read the canned reviewer response shipped with the mock fixture
   (``tools.cross_ai.manual.read_paste_response``).
7. Parse the response into typed rows
   (``tools.cross_ai.parse.parse_response``).
8. Merge those rows into the seeded REVIEW.plan.md
   (``tools.cross_ai.manual.merge_findings_into_review``).
9. Render the operator-facing disclosure summary
   (``tools.cross_ai.manual.format_disclosure_summary``).

The round-trip leans on the real helpers — no mocks, no patches — so any
contract drift between the modules surfaces here before it reaches the
skill that orchestrates them.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from tools import redaction
from tools.cross_ai.config import CrossAiConfig
from tools.cross_ai.detect import CLI
from tools.cross_ai.disclosure import build_disclosure
from tools.cross_ai.manual import (
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


def _seed_feature(repo_root: Path) -> Path:
    """Create the ``.forge/features/<id>/`` tree with SPEC, PLAN, state, REVIEW.

    The shapes are the bare minimum each downstream helper requires:
    ``# Intent`` / ``# Acceptance`` / ``# Negative Requirements`` headings
    on the SPEC (matches the prefix-tolerant extractor in the prompt
    builder), one slice with a ``Files in scope:`` line on the PLAN, a
    schema-valid ``state.json``, and a REVIEW.plan.md with a Findings
    table that already has a header + separator + one data row so the
    merge helper can tack new rows on after the existing block.
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
        "- No subprocess invocation from the manual helpers.\n",
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

    # Step 3: shared redaction filter — defaults are safe (no fatal hits)
    # because the seeded SPEC/PLAN carry no secret-shaped text.
    redaction_result = redaction.filter(
        redaction.PromptPayload(text=prompt.body, files=prompt.files_referenced)
    )
    assert redaction_result.fatal_matches == ()
    assert redaction_result.output_text  # non-empty post-scrub body

    # Step 4: pre-dispatch disclosure snapshot.
    disclosure = build_disclosure(
        prompt=prompt,
        redaction_result=redaction_result,
        cli=CLI.codex,
        config=CrossAiConfig(),
    )
    assert disclosure.target is PromptTarget.plan
    assert disclosure.cli is CLI.codex
    assert disclosure.prompt_tokens > 0

    # Step 5: persist the prompt with a fixed clock so the filename is
    # deterministic. The helper returns the absolute path the operator
    # paste-back step will reference.
    fixed_now = datetime(2026, 5, 10, 12, 0, 0, tzinfo=UTC)
    prompt_path = write_prompt_to_disk(prompt, _FEATURE_ID, tmp_path, now=fixed_now)
    assert prompt_path.exists()
    assert prompt_path.name == "plan-2026-05-10T12-00-00Z-prompt.md"
    assert prompt_path.read_text(encoding="utf-8") == prompt.body

    # Step 6: read the canned reviewer response from the repo fixture
    # (intentionally outside ``tmp_path`` — the fixture is a fixed file
    # on disk that simulates the reviewer's pasted output).
    response_text = read_paste_response(_CANNED_RESPONSE)
    assert "| F-1 |" in response_text

    # Step 7: parse the response into typed Finding rows.
    findings = parse_response(response_text, reviewer_id="mock", target="plan")
    assert len(findings) == 2
    assert findings[0].id == "F-1"
    assert findings[0].severity == "HIGH"
    assert findings[1].id == "F-2"
    assert findings[1].severity == "MEDIUM"
    # Source column is dispatcher-injected; verify the override happened.
    assert all(f.source == "external-mock" for f in findings)

    # Step 8: merge the parsed rows into the seeded REVIEW.plan.md.
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

    # Step 9: render the operator-facing disclosure block; the prefix is
    # a stable contract and the prompt path must appear so the operator
    # knows which file to feed the reviewer CLI.
    summary = format_disclosure_summary(disclosure, prompt_path)
    assert summary.startswith("Cross-AI review (manual mode)")
    assert str(prompt_path) in summary

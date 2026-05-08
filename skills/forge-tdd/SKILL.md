---
name: forge-tdd
description: Enforce TDD discipline inside per-task execute subagent dispatches while keeping every commit green-bisectable. Use whenever a task that produces production code is dispatched. Pairs with forge-context-budget and forge-subagent-dispatch.
disable-model-invocation: true
---

# FORGE TDD Enforcement

## Rule

Every execute-phase task that touches production code under `tools/`, `hooks/`, or `schemas/` MUST follow a **green-bisectable** red→green→refactor cycle: capture RED as a JSONL evidence record (no failing-test commit), then land a paired **test commit** (failing test wrapped in `@pytest.mark.xfail(strict=True)` so `make check` stays green) followed by an atomic **impl commit** that removes the xfail marker and adds the production code. The validator `tools.validate.tdd_evidence` enforces pairing, RED-state evidence, and marker discipline; `forge-execute` step 4 calls it as a BLOCK-level gate. Process details (Prove-It pattern, fixture conventions, naming) live in [`.agents/skills/test-driven-development`](../../.agents/skills/test-driven-development/SKILL.md) — do not restate them here.

## Required dispatch step block

`forge-execute` injects this scaffold into every per-task subagent dispatch's `# Steps` section via `tools.skill_loader.load_scaffold`. Single source of truth lives between the markers below — no inline copies elsewhere.

<!-- scaffold:begin -->
```
1. RED CAPTURE (no commit). Author the failing test for AC <id> in <test path>.
   Run `<scoped pytest command>` and observe failure. Append
   `{ac_id, command, failing_tests:[...], failure_count, recorded_at, commit_sha: null}`
   to `.forge/features/<id>/tdd_evidence.jsonl`. Confirm failure_count > 0; if 0,
   RED was never red — abort and reshape the test.
2. GREEN-VIA-XFAIL TEST COMMIT. Add `@pytest.mark.xfail(strict=True,
   reason="RED for AC-<id>; pairs with next commit")` to the failing test so
   `make check` stays green (xfail counts as expected). Commit
   `test(<scope>): <ac-id> <summary>` with `commit_role: "test"`,
   `paired_with: null`. Append the new SHA to the prior tdd_evidence.jsonl
   record's `commit_sha` field via `tools.state` helper.
3. IMPL COMMIT (atomic). Single commit removes the xfail marker AND adds the
   production code under `tools/`, `hooks/`, or `schemas/`. Run scoped tests;
   assert all previously-xfailed now pass cleanly. Commit
   `feat(<scope>): <ac-id> <summary>` with `commit_role: "impl"`,
   `paired_with: <test-sha>`. `make check` green throughout.
4. Return `{"ac_id": "<id>", "test_commit": "<sha>", "impl_commit": "<sha>",
   "scoped_test_pass": <int>, "scoped_test_fail": 0}`.
```
<!-- scaffold:end -->

Conventional Commits format is mandatory per [`.agents/skills/git-conventions`](../../.agents/skills/git-conventions/SKILL.md): scope from the canonical table (`tools`, `hooks`, `schema`, etc.), imperative subject, target ≤72 chars, no `Co-Authored-By: Claude` trailer.

## Budget contract requirement

Execute-phase budgets MUST include `tests_in_scope: string[]` listing the test files the task will create or modify. The `hooks/check_budget.py` PreToolUse hook rejects execute-phase dispatches missing the field (or with empty array) unless the budget block also carries `tdd_exception_ref: "<ADR-id>"`. See [`forge-context-budget`](../forge-context-budget/SKILL.md) for the full budget shape; this skill only adds the execute-phase requirement.

```text
context_budget:
{
  "phase": "execute",
  "spec_sections": ["Acceptance.AC-3"],
  "files_in_scope": ["tools/state.py"],
  "tests_in_scope": ["tests/tools/test_state.py"],
  "forbidden": ["read entire repo"],
  "return_format": { "max_words": 500 }
}
```

## Whitelisted commit roles

Only two roles skip the test/impl pairing requirement:

- **`commit_role: "docs"`** — pure prose / comment-only. Diff must touch only `docs/**`, `*.md` outside production paths, `*.rst`, OR comment-only changes inside production paths. If a `docs` commit modifies any `.py`, `.json` schema, `.toml`, or hook file beyond comment-only changes, the validator emits a `LOW`-severity finding and (for substantive production-code changes) downgrades to `BLOCK`.
- **`commit_role: "chore"`** — build / dependency only (Makefile churn, dep bumps, repo scaffolding). Diff must touch zero `.py` and zero `.json` schema content.

`refactor` is **not** whitelisted. Refactor commits MUST pair with a test demonstrating behavior preservation, OR carry a documented TDD Exception ADR. The previous `refactor` exemption was removed because "preserving behavior" without a test is reviewer-trust, not enforcement.

## Escape hatch — TDD Exception ADR

When pairing genuinely does not fit (see "What 'too small' looks like" below), document the escape in `decisions.md` before the impl commit lands:

```markdown
## TDD Exception: AC-<id>

- **Rationale:** <one-paragraph why a paired test would be wasteful or infeasible>
- **Reviewer:** <human reviewer name or handle>
- **Date:** <YYYY-MM-DD>
```

The validator parses `decisions.md` for `## TDD Exception: <ac-id>` H2 headings and requires all three keys (`Rationale`, `Reviewer`, `Date`) to be present. Missing keys → finding fails open with explanation. The ADR file must exist before dispatch — the loader inspects it; "I'll write the ADR after" is not honored.

If the budget block declared `tdd_exception_ref: "<ADR-id>"`, the validator cross-checks that every AC implemented by the dispatched task is listed in the named ADR. Budget-level exception cannot silently cover ACs that the ADR does not enumerate.

## What "too small" looks like

Tasks where a paired test feels disproportionate to the change. Two paths out:

- **Whitelist (no ADR needed).** Pure type-rename behind a `Literal` alias, dependency bump, README typo fix, comment-only clarification inside `tools/`. Use `commit_role: "docs"` or `"chore"` per the whitelist above.
- **ADR'd exception.** Two-line bug fix where the reproducing test is longer than the fix and adds no future regression value beyond the diff itself. Schema-only constraint tightening already covered by an existing schema-shape test. In both cases, write the `## TDD Exception` ADR with a concrete rationale — "test would be longer than fix" is a real reason; "I don't feel like writing a test" is not.

When in doubt, write the test. The whitelist is intentionally narrow; the ADR path is intentionally annoying. Both are cheaper than a behavior change shipping with no executable contract.

## Why this rule exists

- Agents drift. "Implement via TDD" as prose advice gets skipped under budget pressure, deadline pressure, or simple inattention. Self-review still passes if some test exists and is green at the end.
- Mechanical pairing is the only durable enforcement. Validator + budget hook + paired commits + RED-state JSONL turn TDD into something the system observes, not something the agent promises.
- Green-bisectable history matters. Standalone failing-test commits break `git bisect` and CI on intermediate SHAs; the xfail-then-impl pattern keeps every commit on the branch green while preserving the RED→GREEN ordering as evidence.
- Squash erases all of this. The `tools.ship_gate` squash-detection guard (S0.7) and harden's pre-flight check refuse to operate on squashed history. Squash → no per-commit pairing → no harden.

See [`AGENTS.md`](../../AGENTS.md) §Testing for the `make check` floor; [`.agents/skills/test-driven-development`](../../.agents/skills/test-driven-development/SKILL.md) for RED→GREEN→REFACTOR mechanics, the Prove-It pattern, and FORGE-specific fixture conventions; [`.agents/skills/git-conventions`](../../.agents/skills/git-conventions/SKILL.md) for the canonical scope table and the ban on `Co-Authored-By: Claude` trailers.

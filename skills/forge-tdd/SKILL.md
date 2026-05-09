---
name: forge-tdd
description: Enforce TDD discipline inside per-task execute subagent dispatches by requiring a paired test→impl commit on every acceptance criterion. Use whenever a task that produces production code is dispatched. Pairs with forge-context-budget and forge-subagent-dispatch.
disable-model-invocation: true
---

# FORGE TDD Enforcement

## Rule

Every execute-phase task that touches production code under `tools/`,
`hooks/`, or `schemas/` MUST land at least one **test commit** before its
matching **impl commit** for each acceptance criterion the task implements.
Both commits are recorded in `.forge/features/<id>/state.json` under
`commits[]` and mapped to the AC in `.forge/features/<id>/slice-<N>.summary`.
The validator `tools.validate.tdd_evidence` enforces the pairing rule;
`forge-execute` step 4 calls it as a `BLOCK`-level gate. Process details
(Prove-It pattern, fixture conventions, naming) live in
[`.agents/skills/test-driven-development`](../../.agents/skills/test-driven-development/SKILL.md)
— do not restate them here.

## What the validator actually checks

The validator at `tools/validate/tdd_evidence.py` performs the following
mechanical checks against `.forge/features/<id>/`:

1. For each AC declared in `SPEC.md` or referenced by any
   `slice-<N>.summary`: an `impl` commit (`feat(...)` / `fix(...)`) must
   have a `test(...)` commit recorded earlier in `state.commits[]`
   insertion order. Missing test → `BLOCK tdd_evidence:missing_test_pair`.
2. A `refactor(...)` commit whose diff touches a production path
   (`src/` / `tools/` / `hooks/` / `schemas/`) is treated as impl-equivalent
   and requires the same paired test. Missing test →
   `BLOCK tdd_evidence:refactor_unpaired`.
3. Every AC declared in `SPEC.md` must be mapped in some
   `slice-<N>.summary` whenever execute-phase commits exist. Unmapped AC →
   `BLOCK tdd_evidence:ac_unmapped_to_slice`.
4. Every test/impl commit recorded in `state.commits[]` (phase=`execute`)
   must be referenced by some `slice-<N>.summary`. Orphan commit →
   `BLOCK tdd_evidence:orphan_commit_no_slice`.
5. A `## TDD Exception: <ac-id>` section in `decisions.md` opts an AC out
   of the pairing rule, but only when the section body declares
   `Rationale`, `Reviewer`, and `Date` (each non-empty). Missing keys →
   `BLOCK tdd_evidence:exception_keys_missing`.

Pairing is decided by `state.commits[]` insertion order — the canonical
chronology recorded by `tools.state.append_commit` — not by the
second-precision `logged_at` timestamp, which collides for
fast-batched commits.

What the validator does NOT yet enforce (tracked as follow-up work, not
silently relied on):

- RED-state JSONL evidence records.
- `@pytest.mark.xfail(strict=True)` marker presence on the failing test
  commit or its removal in the impl commit.
- `commit_role` / `paired_with` metadata on `state.commits[]` entries.
- Cross-checking a budget-level `tdd_exception_ref` against the ACs
  enumerated in the named ADR.
- Squash-history detection.

## Required dispatch step block

`forge-execute` injects the scaffold below into every per-task subagent
dispatch's `# Steps` section. Until a programmatic loader exists, the
orchestrator copies the text verbatim between the markers; do not maintain
inline copies in other skills or templates.

<!-- scaffold:begin -->
```
1. RED. Author the failing test for AC <id> in <test path>. Run
   `<scoped pytest command>` and observe failure. Confirm at least one
   assertion fails; if zero, RED was never red — abort and reshape the
   test.
2. TEST COMMIT. Commit `test(<scope>): <ac-id> <summary>` containing only
   files under `tests/`. The commit is recorded in `state.commits[]` with
   phase=`execute`.
3. IMPL COMMIT. Add the production code under `tools/`, `hooks/`, or
   `schemas/`. Run scoped tests; assert all previously-failing tests now
   pass. Commit `feat(<scope>): <ac-id> <summary>`. Record both SHAs in
   `state.commits[]` and add `<ac-id>: <sha>` lines to
   `.forge/features/<id>/slice-<N>.summary`.
4. Return `{"ac_id": "<id>", "test_commit": "<sha>", "impl_commit": "<sha>",
   "scoped_test_pass": <int>, "scoped_test_fail": 0}`.
```
<!-- scaffold:end -->

Conventional Commits format is mandatory per
[`.agents/skills/git-conventions`](../../.agents/skills/git-conventions/SKILL.md):
scope from the canonical table (`tools`, `hooks`, `schema`, etc.),
imperative subject, target ≤72 chars, no `Co-Authored-By: Claude` trailer.

## Budget contract requirement

Execute-phase budgets MUST include `tests_in_scope: string[]` listing the
test files the task will create or modify. The `hooks/check_budget.py`
PreToolUse hook rejects execute-phase dispatches missing the field (or
with empty array) unless the budget block also carries
`tdd_exception_ref: "<ADR-id>"`. See
[`forge-context-budget`](../forge-context-budget/SKILL.md) for the full
budget shape; this skill only adds the execute-phase requirement.

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

Two roles skip the pairing requirement:

- `docs` — pure prose / comment-only. Diff should touch only `docs/**`,
  `*.md` outside production paths, `*.rst`, OR comment-only changes inside
  production paths.
- `chore` — build / dependency only (Makefile churn, dep bumps, repo
  scaffolding). Diff should touch zero `.py` and zero `.json` schema
  content.

`refactor` is **not** whitelisted: a refactor commit whose diff touches
production paths must pair with a preceding test commit (or carry a TDD
Exception ADR). Refactor commits whose diff touches only non-production
paths (e.g. `tests/`) are treated as `INFO`-level advisory.

The `docs` / `chore` whitelist is currently honored by convention only —
the validator exempts these subjects from the impl-pairing rule but does
not yet inspect the diff to confirm the commit really is comment-only or
build-only. Authors are expected to follow the whitelist; reviewers
catch violations.

## Escape hatch — TDD Exception ADR

When pairing genuinely does not fit (see "What 'too small' looks like"
below), document the escape in `decisions.md` before the impl commit
lands. The validator parses `## TDD Exception: <ac-id>` H2 sections and
requires every section to declare:

```markdown
## TDD Exception: AC-<id>

- Rationale: <one paragraph why a paired test would be wasteful or infeasible>
- Reviewer: <human reviewer name or handle>
- Date: <YYYY-MM-DD>
```

A bare heading or any section with one of those three keys missing or
empty produces `BLOCK tdd_evidence:exception_keys_missing`.

If the budget block declared `tdd_exception_ref: "<ADR-id>"`, the budget
hook accepts an empty `tests_in_scope` for that dispatch. Cross-checking
that the ADR enumerates every AC the dispatch implements is not yet
performed by the validator — reviewers verify it manually.

## What "too small" looks like

Tasks where a paired test feels disproportionate to the change. Two paths
out:

- **Whitelist (no ADR needed).** Pure type-rename behind a `Literal` alias,
  dependency bump, README typo fix, comment-only clarification inside
  `tools/`. Use a `docs` or `chore` subject prefix per the whitelist
  above.
- **ADR'd exception.** Two-line bug fix where the reproducing test is
  longer than the fix and adds no future regression value beyond the diff
  itself. Schema-only constraint tightening already covered by an existing
  schema-shape test. In both cases, write the `## TDD Exception` ADR with
  a concrete rationale — "test would be longer than fix" is a real
  reason; "I don't feel like writing a test" is not.

When in doubt, write the test. The whitelist is intentionally narrow; the
ADR path is intentionally annoying. Both are cheaper than a behavior
change shipping with no executable contract.

## Why this rule exists

- Agents drift. "Implement via TDD" as prose advice gets skipped under
  budget pressure, deadline pressure, or simple inattention. Self-review
  still passes if some test exists and is green at the end.
- Mechanical pairing is the only durable enforcement. Validator + budget
  hook + paired commits turn TDD into something the system observes, not
  something the agent promises.

See [`AGENTS.md`](../../AGENTS.md) §Testing for the `make check` floor;
[`.agents/skills/test-driven-development`](../../.agents/skills/test-driven-development/SKILL.md)
for RED→GREEN→REFACTOR mechanics, the Prove-It pattern, and FORGE-specific
fixture conventions;
[`.agents/skills/git-conventions`](../../.agents/skills/git-conventions/SKILL.md)
for the canonical scope table and the ban on `Co-Authored-By: Claude`
trailers.

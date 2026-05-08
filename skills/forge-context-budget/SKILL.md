---
name: forge-context-budget
description: Refuse subagent dispatches that lack a context_budget block, or whose budget is unbounded. Use whenever you are about to spawn an Agent subagent during /forge:execute, /forge:verify, or any FORGE review. Pairs with the hooks/check_budget.py PreToolUse hook, which performs the mechanical deny.
---

# FORGE Context Budget Enforcement

## Rule

Every subagent dispatch fired during an FORGE phase MUST include a `context_budget:` JSON block at the top of the dispatch prompt. The `PreToolUse` hook (see `hooks/check_budget.py`) blocks dispatches that lack the block, lack `files_in_scope`, leave `forbidden` empty, or set `files_in_scope` to an unbounded glob. This skill describes the contract; the hook enforces it. The block is JSON (not YAML) so the hook stays stdlib-only and parses dispatches with `json.loads` — no third-party YAML dep.

## Required block

A literal `context_budget:` marker at column 0, followed by a JSON object. The marker line must not be inside a fenced code block (` ``` `).

```text
context_budget:
{
  "phase": "execute",
  "spec_sections": ["<list of SPEC.md section names this task needs>"],
  "files_in_scope": ["<exact paths or globs>"],
  "tests_in_scope": ["<test file paths the dispatch will create or modify>"],
  "tdd_exception_ref": "<ADR-id, only when execute-phase tests_in_scope is empty>",
  "intel_files": ["<paths under .forge/intel/, or omit if none>"],
  "decisions_filter": "tag:<topic> | all | none",
  "prior_summaries": ["<slice-N.summary or wave-N.summary>"],
  "forbidden": [
    "read entire repo",
    "load all specs"
  ],
  "return_format": {
    "commits_made": "list[sha + one-line subject]",
    "decisions": "list[ref ids in decisions.md]",
    "deviations": "list[{cause: str, resolution: str}]",
    "next_recommendations": "optional list[str]",
    "max_words": 500
  }
}
```

The hook validates `files_in_scope` (non-empty array of bounded globs), `forbidden` (non-empty array), and the execute-phase `tests_in_scope` rule below. The other keys are part of the FORGE contract but not hook-enforced — they are read by the dispatched subagent.

## Phase discriminator and TDD scope fields

Three fields drive execute-phase TDD enforcement. Schema reference: [`schemas/budget.schema.json`](../../schemas/budget.schema.json) — documentation only; the hook keeps a stdlib-only frozen literal copy of the phase value it cares about (`"execute"`).

- **`phase`** *(optional but recommended)* — the FORGE phase under which this dispatch runs. Enum mirrors `current_phase` in `schemas/state.schema.json` (`refine`, `research`, `spec`, `domain`, `scenarios`, `plan`, `crucible`, `review`, `execute`, `verify`, `ship`, `harden`). Required to opt into execute-phase enforcement; absent or non-`execute` values leave `tests_in_scope` optional.
- **`tests_in_scope`** *(string array)* — test files the subagent will create or modify. **Mandatory and non-empty when `phase == "execute"`**, unless `tdd_exception_ref` is set. The hook denies execute-phase dispatches that lack the field, set it to a non-array, or leave it empty without an exception. For non-execute phases the field is optional (but if present it must still be a list of strings).
- **`tdd_exception_ref`** *(string)* — ADR id from `.forge/features/<id>/decisions.md` (`## TDD Exception: <ac-id>` heading) that justifies an empty `tests_in_scope` under execute phase. The validator `tools.validate.tdd_evidence` cross-checks that every AC the dispatch implements is listed in the named ADR — a budget-level exception cannot silently cover ACs the ADR does not enumerate. See [`forge-tdd`](../forge-tdd/SKILL.md) for the full RED→GREEN scaffold and ADR shape requirements.

## What to do when the hook blocks

The hook returns a PreToolUse deny decision with a `permissionDecisionReason`. To proceed:

1. Read the reason printed in the block message.
2. Edit your dispatch prompt to satisfy the rule (add the block, narrow `files_in_scope`, populate `forbidden`).
3. Re-issue the dispatch.

Never bypass the hook. If you cannot determine a tight scope, ask the user. Never default to "read everything".

## What "too broad" looks like

A budget is too broad when:

- `files_in_scope` is `["**"]`, `["*.py"]` repo-wide, or absent.
- `spec_sections` is `["all"]` or absent.
- `intel_files` includes more than three files or asks for `INDEX.md` plus everything.
- `forbidden` is empty.

When too broad, narrow before dispatching. The default upper bound for `files_in_scope` is **5 paths or 1 directory glob**. Anything larger requires an explicit reason logged in `decisions.md`.

## Exception: explicit user override

If the user explicitly tells you to widen the budget (for example, "scan the whole repo"), comply — but log the override to `decisions.md` with a one-line rationale. The hook does not override; only the user does, by telling you to use a wider scope at the prompt level (which the hook will still parse).

## Why this rule exists

- The main thread holds only a slice index plus per-slice summaries. Real work happens in subagents.
- A subagent that loads the entire repo undermines the architecture and bloats both the subagent's context and the eventual summary.
- The budget block is the contract that keeps execution sliceable, summarizable, and resumable.

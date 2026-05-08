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
  "spec_sections": ["<list of SPEC.md section names this task needs>"],
  "files_in_scope": ["<exact paths or globs>"],
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

The hook validates only `files_in_scope` (non-empty array of bounded globs) and `forbidden` (non-empty array). The other keys are part of the FORGE contract but not hook-enforced — they are read by the dispatched subagent.

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

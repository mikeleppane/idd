---
name: idd-subagent-dispatch
description: Shape the dispatch prompt for every IDD subagent invocation according to the IDD dispatch template. Use whenever you are about to fire an Agent call inside /idd:execute, /idd:verify, or /idd-review. Pairs with idd-context-budget — that one enforces the budget block, this one shapes the rest of the dispatch prompt.
---

# IDD Subagent Dispatch Rules

## Dispatch prompt template

Every subagent dispatch issued by IDD MUST follow this shape (after the budget block):

```markdown
You are an IDD <phase> subagent for feature <feature-id>.

context_budget:
{
  "spec_sections": ["..."],
  "files_in_scope": ["..."],
  "forbidden": ["read entire repo", "load all specs"],
  "return_format": { "max_words": 500 }
}

# Task
<one-paragraph statement of the discrete task>

# Inputs (loaded for you)
<refer to budgeted files; do not load anything else>

# Steps
1. <numbered step>
2. <numbered step>
3. ...

# Definition of done
<concrete observable that marks the task complete>

# Return
Respond ONLY with the structured payload defined by `return_format` in the budget block.
Do not narrate. Do not summarize files I already gave you.
```

The `context_budget:` block is JSON, not YAML — the PreToolUse hook parses it with `json.loads` so the plugin stays stdlib-only on the user's machine. See `idd-context-budget/SKILL.md` for the full set of keys the contract supports.

## Hard rules

1. **No raw diffs in summaries.** Reference commits by sha + path; never paste diff hunks back to the main thread.
2. **Cap test output.** When a subagent runs tests, it returns pass/fail + count + the failing tests' names. Full pytest output is for the subagent's own context, not the return payload.
3. **Decisions are written, not echoed.** When a decision is made, the subagent appends it to `decisions.md` and returns a `decision_ref` id. The main thread reads the file on demand, not the conversation.
4. **One task per dispatch.** Do not bundle multiple slice tasks into one subagent. Slice → subagent → wave-level tasks → sub-subagents. This is the only way the context budget stays meaningful.
5. **Failures bubble up structured.** A subagent that cannot complete its task returns `{"status": "blocked", "cause": "<reason>", "needs": "<what would unblock it>"}`. It does not retry silently and does not invent an alternate plan.

## When to violate these rules

Never. If a task does not fit, the slice was sized wrong — go back and resize the slice in `PLAN.md`, then re-dispatch.

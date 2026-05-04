# AGENTS.md — IDD Discovery Manifest

This file lets non-Claude tools (Cursor, Aider, Codex) discover the same IDD skills and commands the Claude Code plugin uses. The skills and commands are pure markdown — no build step, no codegen.

> **Status:** Cursor/Aider/Codex compatibility is documented intent in M1; live verification ships in M5.

## Skills (directory-per-skill, body in `skills/<name>/SKILL.md`)

| Name | Path | Auto-load | Purpose |
|---|---|---|---|
| `idd-spec`              | `skills/idd-spec/SKILL.md`              | default | Author a feature SPEC.md following the IDD template. |
| `idd-execute`           | `skills/idd-execute/SKILL.md`           | explicit | Focused-tier execute: implement directly from SPEC.md acceptance criteria. |
| `idd-verify`            | `skills/idd-verify/SKILL.md`            | explicit | Three-layer verification: code-audit + scenario execution + UAT. |
| `idd-context-budget`    | `skills/idd-context-budget/SKILL.md`    | default | Refuse subagent dispatches that lack a context-budget block. |
| `idd-subagent-dispatch` | `skills/idd-subagent-dispatch/SKILL.md` | default | Helper rules for dispatching context-bounded subagents. |

"Default" auto-load = Claude Code may invoke based on description match. "Explicit" = `disable-model-invocation: true` in frontmatter; only invoked through commands or by name.

## Commands (named workflows, flat markdown)

| Slash | Path | Purpose |
|---|---|---|
| `/idd:spec`    | `commands/spec.md`    | Run the spec phase: write `.idd/features/<id>/SPEC.md`. |
| `/idd:execute` | `commands/execute.md` | Run the execute phase against the active feature. |
| `/idd:verify`  | `commands/verify.md`  | Run the verify phase against the active feature. |

## Templates

| Path | Description |
|---|---|
| `templates/feature/SPEC.md`         | Source-of-truth template — Intent, Context, Domain, Codebase Anchors, Scope, Scenarios, Test Strategy, Acceptance Criteria, Negative Requirements, Open Questions, Decisions. |
| `templates/feature/PLAN.md`         | Slice + waves plan template (used in M2). |
| `templates/feature/VERIFICATION.md` | Acceptance-criteria audit table. |
| `templates/feature/decisions.md`    | Append-only ADR-lite log. |
| `templates/feature/state.json`      | Phase machine state per feature. |

## Hooks

The `hooks/` directory contains a `PreToolUse` hook that enforces the IDD subagent context-budget contract. In Claude Code it is wired automatically via `.claude-plugin/plugin.json`. In other tools, run `python hooks/check_budget.py` manually before each subagent dispatch.

## Tool Mapping

- **Claude Code:** `.claude-plugin/plugin.json` is the canonical loader; commands appear as slash commands and skills load via Claude Code's skill discovery.
- **Cursor:** reference skills via `@skills/idd-spec/SKILL.md` etc. Commands are documented prompts; copy the body of `commands/spec.md` when running.
- **Aider:** load `skills/<name>/SKILL.md` as system prompts; run commands by pasting their contents.
- **Codex CLI:** use `--system` to load a chosen skill, then prompt with the command body.

Full per-tool portability validation lands in M5.

# AGENTS.md — IDD Discovery Manifest

This file lets non-Claude tools (Cursor, Aider, Codex) discover the same IDD skills and commands the Claude Code plugin uses. The skills and commands are pure markdown — no build step, no codegen.

> **Status:** Cursor/Aider/Codex compatibility is documented intent in M1; live verification ships in M5.

## Skills (directory-per-skill, body in `skills/<name>/SKILL.md`)

| Name | Path | Auto-load | Purpose |
|---|---|---|---|
| `idd-spec`              | `skills/idd-spec/SKILL.md`              | default | Author a feature SPEC.md following the IDD template. |
| `idd-scenarios`         | `skills/idd-scenarios/SKILL.md`         | explicit | Standard-tier scenarios: expand SPEC.md § Scenarios into rigorous Gherkin and emit `.feature` files when a BDD framework is detected. |
| `idd-plan`              | `skills/idd-plan/SKILL.md`              | explicit | Standard-tier plan: file-bound, acceptance-mapped slice + wave PLAN.md with a Verified Dependencies section. |
| `idd-crucible`          | `skills/idd-crucible/SKILL.md`          | explicit | Standard-tier crucible ritual: assumptions inversion → adversarial Q&A → pre-mortem, producing UNDERSTANDING.md. |
| `idd-review`            | `skills/idd-review/SKILL.md`            | explicit | Standard-tier review: cheap self-review + optional heavy subagent pass + convergence loop on HIGH+ findings (max 3 cycles). Targets PLAN.md or code diff. |
| `idd-execute`           | `skills/idd-execute/SKILL.md`           | explicit | Run the execute phase. Focused tier drives directly from SPEC.md acceptance criteria; standard / full tiers drive slice-by-slice from PLAN.md with wave parallelism and per-subagent context budgets. |
| `idd-verify`            | `skills/idd-verify/SKILL.md`            | explicit | Three-layer verification: code-audit + Layer 2 scenario execution (when a BDD framework is detected via `tools.bdd_detect`) + conversational UAT. |
| `idd-ship`              | `skills/idd-ship/SKILL.md`              | explicit | Standard-tier ship: write the canonical capability SPEC.md and archive the feature folder. M2 supports first-ship only; delta proposals are M3+. |
| `idd-next`              | `skills/idd-next/SKILL.md`              | explicit | Resolve and print or dispatch the next phase command from `state.json`. Read-only. |
| `idd-status`            | `skills/idd-status/SKILL.md`            | explicit | One-line status of the active feature: phase, tier, last commit. Read-only. |
| `idd-validate`          | `skills/idd-validate/SKILL.md`          | explicit | Run the structural validator over IDD artifacts (Constitution, delta, NR, capability uniqueness, repo health). Read-only. |
| `idd-context-budget`    | `skills/idd-context-budget/SKILL.md`    | default | Refuse subagent dispatches that lack a context-budget block. |
| `idd-subagent-dispatch` | `skills/idd-subagent-dispatch/SKILL.md` | default | Helper rules for dispatching context-bounded subagents. |

"Default" auto-load = Claude Code may invoke based on description match. "Explicit" = `disable-model-invocation: true` in frontmatter; only invoked through commands or by name.

## Commands (named workflows, flat markdown)

| Slash | Path | Purpose |
|---|---|---|
| `/idd:spec`      | `commands/spec.md`      | Run the spec phase: write `.idd/features/<id>/SPEC.md`. |
| `/idd:scenarios` | `commands/scenarios.md` | Run the scenarios phase: expand SPEC.md § Scenarios into Gherkin and (when supported) `.feature` files. Standard/full tier only. |
| `/idd:plan`      | `commands/plan.md`      | Run the plan phase: author PLAN.md with vertical slices, waves, and Verified Dependencies. Standard/full tier only. |
| `/idd:crucible`  | `commands/crucible.md`  | Run the crucible phase: three-step adversarial ritual producing UNDERSTANDING.md. Standard/full tier only. |
| `/idd:review`    | `commands/review.md`    | Run the review phase against the active feature. `--target plan` (default after crucible) or `--target code` (default after execute). Cross-AI review is M4 territory. |
| `/idd:execute`   | `commands/execute.md`   | Run the execute phase against the active feature. |
| `/idd:verify`    | `commands/verify.md`    | Run the verify phase against the active feature. |
| `/idd:ship`      | `commands/ship.md`      | Run the ship phase: write the canonical capability SPEC.md and archive the feature. First-ship only in M2; delta proposals are M3+. |
| `/idd:next`      | `commands/next.md`      | Show or run the next phase command for the active feature. Flags: `--feature <id>`, `--run`. |
| `/idd:status`    | `commands/status.md`    | One-line feature status: phase, tier, last commit. Flags: `--feature <id>`, `--verbose`. |
| `/idd:validate`  | `commands/validate.md`  | Run the structural validator. Flags: `--target <spec\|plan\|delta\|constitution\|ship\|health\|all>`, optional path, `--repo-root <path>`. Exit 0 / 1 (BLOCK\|HIGH) / 2 (usage). |

## Templates

| Path | Description |
|---|---|
| `templates/feature/SPEC.md`         | Source-of-truth template — Intent, Context, Domain, Codebase Anchors, Scope, Scenarios, Test Strategy, Acceptance Criteria, Negative Requirements, Open Questions, Decisions. |
| `templates/feature/PLAN.md`         | Slice + waves plan template (used in M2). |
| `templates/feature/VERIFICATION.md` | Acceptance-criteria audit table. |
| `templates/feature/decisions.md`    | Append-only ADR-lite log. |
| `templates/feature/state.json`      | Phase machine state per feature. |

## Hooks

The `hooks/` directory contains a `PreToolUse` hook that enforces the IDD subagent context-budget contract. In Claude Code it is wired automatically via `.claude-plugin/plugin.json`. In other tools, run `python3 hooks/check_budget.py` manually before each subagent dispatch.

## Tool Mapping

- **Claude Code:** `.claude-plugin/plugin.json` is the canonical loader; commands appear as slash commands and skills load via Claude Code's skill discovery.
- **Cursor:** reference skills via `@skills/idd-spec/SKILL.md` etc. Commands are documented prompts; copy the body of `commands/spec.md` when running.
- **Aider:** load `skills/<name>/SKILL.md` as system prompts; run commands by pasting their contents.
- **Codex CLI:** use `--system` to load a chosen skill, then prompt with the command body.

Full per-tool portability validation lands in M5.

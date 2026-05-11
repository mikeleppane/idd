# AGENTS.md — FORGE Discovery Manifest

This file lets non-Claude tools (Cursor, Aider, Codex) discover the same FORGE skills and commands the Claude Code plugin uses. The skills and commands are pure markdown — no build step, no codegen.

> **Status:** Cursor/Aider/Codex compatibility is documented intent in M1; live verification ships in M5.

## Skills (directory-per-skill, body in `skills/<name>/SKILL.md`)

| Name | Path | Auto-load | Purpose |
|---|---|---|---|
| `forge-spec`              | `skills/forge-spec/SKILL.md`              | default | Author a feature SPEC.md following the FORGE template. |
| `forge-scenarios`         | `skills/forge-scenarios/SKILL.md`         | explicit | Standard-tier scenarios: expand SPEC.md § Scenarios into rigorous Gherkin and emit `.feature` files when a BDD framework is detected. |
| `forge-plan`              | `skills/forge-plan/SKILL.md`              | explicit | Standard-tier plan: file-bound, acceptance-mapped slice + wave PLAN.md with a Verified Dependencies section. |
| `forge-crucible`          | `skills/forge-crucible/SKILL.md`          | explicit | Standard-tier crucible ritual: assumptions inversion → adversarial Q&A → pre-mortem, producing UNDERSTANDING.md. |
| `forge-research`          | `skills/forge-research/SKILL.md`          | explicit | Codebase + external library discovery before spec; emits RESEARCH.md with mode-aware citations. Full tier auto; standard with `--research`; focused refused. |
| `forge-review`            | `skills/forge-review/SKILL.md`            | explicit | In-house heavy-pass review with convergence loop on HIGH+ findings (max 3 cycles). Targets PLAN.md or code diff. `--cross-ai` flag delegates to manual or auto external CLI per config; `--cross-ai-paste <path>` merges externally-generated findings. |
| `forge-execute`           | `skills/forge-execute/SKILL.md`           | explicit | Run the execute phase. Focused tier drives directly from SPEC.md acceptance criteria; standard / full tiers drive slice-by-slice from PLAN.md with wave parallelism and per-subagent context budgets. |
| `forge-verify`            | `skills/forge-verify/SKILL.md`            | explicit | Three-layer verification: code-audit + Layer 2 scenario execution (when a BDD framework is detected via `tools.bdd_detect`) + conversational UAT. |
| `forge-ship`              | `skills/forge-ship/SKILL.md`              | explicit | Standard-tier ship: write the canonical capability SPEC.md and archive the feature folder. M2 supports first-ship only; delta proposals are M3+. |
| `forge-next`              | `skills/forge-next/SKILL.md`              | explicit | Resolve and print or dispatch the next phase command from `state.json`. Read-only. |
| `forge-status`            | `skills/forge-status/SKILL.md`            | explicit | One-line status of the active feature: phase, tier, last commit. Read-only. |
| `forge-validate`          | `skills/forge-validate/SKILL.md`          | explicit | Run the structural and semantic validator over FORGE artifacts. Per-file targets: `spec`, `plan`, `delta`, `scenarios`, `anchors`, `spec-semantic`, `plan-tasks`, `verified-deps`. Per-folder: `deviations`. Repo-wide: `constitution`, `ship`, `health`, `all`. `--check-registries` opt-in for live registry probes (offline default). Read-only. |
| `forge-context-budget`    | `skills/forge-context-budget/SKILL.md`    | default | Refuse subagent dispatches that lack a context-budget block. |
| `forge-subagent-dispatch` | `skills/forge-subagent-dispatch/SKILL.md` | default | Helper rules for dispatching context-bounded subagents. |
| `forge-constitution`        | `skills/forge-constitution/SKILL.md`        | doc-only | Documents the loader+filter contract. Phase skills invoke `tools.constitution.load_and_filter` directly; this skill has `disable-model-invocation: true`. |
| `forge-amend-constitution`  | `skills/forge-amend-constitution/SKILL.md`  | explicit | $EDITOR-driven Constitution edits with atomic-pair (Constitution + decisions.md) write, semver bump. `--bootstrap` mode defers to `forge-bootstrap-constitution` for first-time drafting; refuses if a Constitution already exists. |
| `forge-bootstrap-constitution` | `skills/forge-bootstrap-constitution/SKILL.md` | explicit | First-time Constitution drafting. Runs bounded signal collection via `tools.constitution_amend.collect_bootstrap_signals`, drafts articles in-session from the payload, then walks the user through a sequential `accept / refine / edit-in-editor / skip / cancel` selector. On accept, persists via `tools.constitution_amend.persist_drafted_constitution` (atomic-pair write of `.forge/CONSTITUTION.md` + decisions.md ADR). Refuses if a Constitution already exists. Refine loop caps at 5 rounds. |
| `forge-change`              | `skills/forge-change/SKILL.md`              | explicit | Author a delta proposal against a canonical capability spec; routed from `/forge:spec` capability scan or invoked directly via `/forge:change`. |
| `forge-refine`              | `skills/forge-refine/SKILL.md`              | explicit | Socratic vague-idea collapse before `/forge:spec`. Pre-spec phase for full-tier features; max 5 rounds, persists `state.json.refined_idea` and increments `routing.refine_attempts`. |
| `forge-domain`              | `skills/forge-domain/SKILL.md`              | explicit | Glossary table + optional Mermaid sketch for full-tier feature SPEC.md. Runs after `/forge:spec` on full tier; populates `# Domain` section in-place. |
| `forge-do`                  | `skills/forge-do/SKILL.md`                  | explicit | Adaptive routing entry point: `/forge:do "<idea>" [--focused \| --standard \| --full]` proposes a tier + phase list via the LLM, runs Constitution and health preflights, scans for capability collisions, then seeds the feature folder via `tools.routing.seed_routed_feature` and dispatches the first phase command for the chosen tier. |

"Default" auto-load = Claude Code may invoke based on description match. "Explicit" = `disable-model-invocation: true` in frontmatter; only invoked through commands or by name.

## Commands (named workflows, flat markdown)

| Slash | Path | Purpose |
|---|---|---|
| `/forge:spec`      | `commands/spec.md`      | Run the spec phase: write `.forge/features/<id>/SPEC.md`. |
| `/forge:scenarios` | `commands/scenarios.md` | Run the scenarios phase: expand SPEC.md § Scenarios into Gherkin and (when supported) `.feature` files. Standard/full tier only. |
| `/forge:plan`      | `commands/plan.md`      | Run the plan phase: author PLAN.md with vertical slices, waves, and Verified Dependencies. Standard/full tier only. |
| `/forge:crucible`  | `commands/crucible.md`  | Run the crucible phase: three-step adversarial ritual producing UNDERSTANDING.md. Standard/full tier only. |
| `/forge:research`  | `commands/research.md`  | Run the research phase: codebase + external library discovery before spec. Emits RESEARCH.md with grounding mode (`full`/`degraded`/`websearch`/`byod`/`byod-partial`). Args: `--feature <id>`, `--skip "<reason>"`. |
| `/forge:review`    | `commands/review.md`    | Run the review phase against the active feature. `--target plan` (default after crucible) or `--target code` (default after execute). `--cross-ai` writes a manual prompt for an external CLI; `--cross-ai --auto` dispatches automatically (opt-in); `--cross-ai-paste <path>` merges an externally-generated response. |
| `/forge:execute`   | `commands/execute.md`   | Run the execute phase against the active feature. |
| `/forge:verify`    | `commands/verify.md`    | Run the verify phase against the active feature. |
| `/forge:ship`      | `commands/ship.md`      | Run the ship phase: write the canonical capability SPEC.md and archive the feature. First-ship only in M2; `--change <id>` merges an approved delta proposal (M3+). |
| `/forge:change`    | `commands/change.md`    | Author a delta proposal for an existing canonical capability. Args: `[--capability <slug>] [<description>]`. |
| `/forge:refine`    | `commands/refine.md`    | Socratic vague-idea collapse before `/forge:spec`. Pre-spec phase for full-tier features. Args: `[--feature <id>]`. |
| `/forge:domain`    | `commands/domain.md`    | Glossary table + optional Mermaid sketch for full-tier feature SPEC.md. Runs after `/forge:spec` on full tier. Args: `[--feature <id>]`. |
| `/forge:do`        | `commands/do.md`        | Adaptive routing entry point: propose a tier + phase list, seed the feature folder, then dispatch `/forge:spec` (focused / standard) or `/forge:refine` (full). Args: `"<idea>" [--focused \| --standard \| --full]`. |
| `/forge:next`      | `commands/next.md`      | Show or run the next phase command for the active feature. Flags: `--feature <id>`, `--run`. |
| `/forge:status`    | `commands/status.md`    | One-line feature status: phase, tier, last commit. Flags: `--feature <id>`, `--verbose`. |
| `/forge:validate`  | `commands/validate.md`  | Run the structural and semantic validator. Flags: `--target <spec\|plan\|delta\|constitution\|ship\|health\|scenarios\|anchors\|spec-semantic\|plan-tasks\|verified-deps\|deviations\|all>`, optional path, `--repo-root <path>`, `--check-registries` (off by default; live registry probes for `verified-deps` / `all`). Exit 0 / 1 (BLOCK\|HIGH) / 2 (usage). |
| `/forge:amend-constitution` | `commands/amend-constitution.md` | Open `.forge/CONSTITUTION.md` in $EDITOR for atomic edit + semver bump + decisions.md ADR entry. Pass `--bootstrap` to defer to `forge-bootstrap-constitution` for first-time drafting (bounded signal collection, in-session drafting, accept/refine/edit/skip/cancel selector). |

## Templates

| Path | Description |
|---|---|
| `templates/feature/SPEC.md`         | Source-of-truth template — Intent, Context, Domain, Codebase Anchors, Scope, Scenarios, Test Strategy, Acceptance Criteria, Negative Requirements, Open Questions, Decisions. |
| `templates/feature/PLAN.md`         | Slice + waves plan template (used in M2). |
| `templates/feature/VERIFICATION.md` | Acceptance-criteria audit table. |
| `templates/feature/decisions.md`    | Append-only ADR-lite log. |
| `templates/feature/state.json`      | Phase machine state per feature. |
| `templates/feature/RESEARCH.md`     | Research artifact: codebase findings, external docs (grounding-mode-aware citations), domain notes, risks. |

## Engineering skills (`.agents/skills/`)

Local engineering-practice skills that govern HOW Python work lands in this repo. Every Python edit, refactor, review, test, or commit MUST consult these skills — not optional, not "if relevant".

| Skill | Path | When to use |
|---|---|---|
| `test-driven-development` | `.agents/skills/test-driven-development/SKILL.md` | Every behavior change. RED → GREEN → REFACTOR. Failing test before code. |
| `coding-guidance-python` | `.agents/skills/coding-guidance-python/SKILL.md` | Every Python file create / modify / review. Type safety, contracts, module boundaries. |
| `git-conventions` | `.agents/skills/git-conventions/SKILL.md` | Every commit. Conventional Commits with required scopes, ASCII-only subjects, target ≤72 chars (soft cap 90 for unusual cases like spec-section anchors). |
| `code-review-and-quality` | `.agents/skills/code-review-and-quality/SKILL.md` | Every review pass before merge. |

Subagent dispatches that touch Python code MUST cite all four in the dispatch brief alongside the task. Skipping these for "small" or "trivial" Python edits is the most common drift mode — do not.

## Hooks

The `hooks/` directory ships two `PreToolUse` hooks. **Claude Code 2.1+ auto-loads `hooks/hooks.json` by path convention** — `.claude-plugin/plugin.json` must NOT redeclare it, or the install fails with "Duplicate hooks file detected" and the plugin silently disables. In other tools, invoke each hook manually before the matching tool call.

- `hooks/check_budget.py` — enforces the FORGE subagent context-budget contract on `Agent` dispatches. The hook is permissive on the optional `articles[]` field carrying filtered Constitution articles for subagent context. Tests in `tests/hooks/test_check_budget_articles.py` pin this permissiveness.
- `hooks/check_state_writer.py` — refuses direct `Write` / `Edit` / `MultiEdit` against `.forge/features/<id>/state.json`. State.json mutations must go through the `tools.state.*` helpers (`complete_phase`, `start_phase`, `record_routing_decision`, `record_refined_idea`, `record_commit`, `append_deviation`, `set_execute_current_slice`); direct file edits bypass schema validation and produce broken seeds.

## Tool Mapping

- **Claude Code:** `.claude-plugin/plugin.json` is the canonical loader; commands appear as slash commands and skills load via Claude Code's skill discovery.
- **Cursor:** reference skills via `@skills/forge-spec/SKILL.md` etc. Commands are documented prompts; copy the body of `commands/spec.md` when running.
- **Aider:** load `skills/<name>/SKILL.md` as system prompts; run commands by pasting their contents.
- **Codex CLI:** use `--system` to load a chosen skill, then prompt with the command body.

Full per-tool portability validation lands in M5.

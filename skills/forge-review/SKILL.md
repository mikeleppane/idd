---
name: forge-review
description: Layered review — cheap self-review pass plus an optional heavy on-demand subagent pass — feeding a convergence loop that drives HIGH+ findings to zero across max 3 cycles. Targets PLAN.md or code diff. Use after /forge:crucible (plan target) or /forge:execute (code target).
disable-model-invocation: true
---

# FORGE Review

## When this skill applies

Active feature is in review phase. Target is `plan` (after crucible) or `code` (after execute).

## Inputs

- `.forge/features/<id>/SPEC.md`, `PLAN.md`, `UNDERSTANDING.md`.
- For target=code: the working tree diff since spec creation, plus `state.commits[]`.
- `templates/feature/REVIEW.md`.

## Output naming

The standard-tier flow runs review twice — once after crucible (`target: plan`) and once after execute (`target: code`). The two passes write to **separate, per-target files** so neither audit trail gets clobbered:

- `target=plan` → `.forge/features/<id>/REVIEW.plan.md`
- `target=code` → `.forge/features/<id>/REVIEW.code.md`

The plain name `.forge/features/<id>/REVIEW.md` is reserved (do not write it). Downstream phases read these per-target files: `/forge:execute` requires `REVIEW.plan.md` with `target: plan`, `status: resolved`; `/forge:verify` requires `REVIEW.code.md` with `target: code`, `status: resolved`.

## Steps

1. **Validate state.** Read `state.json`; abort if not in review phase.
1a. **Constitution preflight.** Call `tools.constitution.load_and_filter(repo_root, idea_text=<spec_intent>, files_in_scope=<spec_anchors_or_plan_files>)`. For target=code, the resulting `articles[]` (serialized via `Article.to_budget_dict()`) MUST be included in the heavy-subagent dispatch budget. **When `len(articles) > 0` AND target=code, the heavy pass is mandatory** — it cannot be skipped on a clean self-review (closes the self-review skip gap; see Open Scoping #13). The reviewer subagent tags every article violation in REVIEW.code.md with `[constitution:A<n>]` (e.g. `[constitution:A1] HIGH src/foo.py:42 — direct session call`). Severity mapping: CRITICAL→HIGH, SHOULD→MEDIUM, MAY→LOW.
2. **Mark active target.** Call `tools.state.set_review_target(path, review_target=<plan|code>)` so `phases.review.current_target` reflects which pass is in flight. Idempotent within an in-progress review — safe to call once per skill invocation.
3. **Copy template** if `REVIEW.<target>.md` does not exist: write `.forge/features/<id>/REVIEW.<target>.md` from `templates/feature/REVIEW.md` with frontmatter: `spec: <feature-id>`, `target: <plan|code>`, `status: open`, `cycles: 1`.
4. **Cycle N — Self-review pass.**
   - For target=plan: walk every slice. Check (a) every acceptance criterion mapped to exactly one slice; (b) every file in scope appears in exactly one slice unless shared; (c) Verified Dependencies non-empty when new deps; (d) wave dependencies make sense (no Wave 2 task depends on a Wave 3 task).
   - For target=code:
     - **Run the structural git-conventions validator** for the feature: `findings = tools.validate.validate_git_conventions(.forge/features/<id>/)`. Merge every returned :class:`Finding` into `REVIEW.code.md` § Findings with `Source: git-conventions`. Severity is taken from the validator (`BLOCK` / `HIGH` / `MEDIUM` / `WARN`). This replaces the previous manual "commit message follows Conventional Commits with allowed scope" self-check — the rule is now mechanical and lives in the validator.
     - **Run the pattern-based conventions validator** against the working diff + commit bodies: `findings = tools.validate.validate_conventions(repo_root, commit_body=<latest_commit_body>, diff=<git_diff_since_spec_created>)`. Merge every returned :class:`Finding` into `REVIEW.code.md` § Findings with `Source: conventions`. The validator silently no-ops when `.forge/conventions.json` is absent.
     - Walk every commit since spec creation. Check (a) commit content matches one PLAN.md task; (b) tests added or modified for new behavior; (c) no obvious misalignment with SPEC § Negative Requirements.
   - Append remaining self-pass findings to the per-target `REVIEW.<target>.md` § Findings with `Source: self`. Severity: BLOCK / HIGH / MEDIUM / LOW.
4a. **Cross-AI dispatch (manual or auto, optional).** Triggered when ANY of: user passes `--cross-ai`, user passes `--auto`, or `.forge/config.json` carries `cross_ai.mode == "auto"`. `mode == "disabled"` REFUSES regardless of flag.
   - **Load config.** Read `.forge/config.json` cross_ai block via `tools.cross_ai.config.load_config(repo_root)` → bound as `config`. Refuse with hint if `config.mode == "disabled"`.
   - **CLI selection.** Call `tools.cross_ai.detect.detect_clis()` + `pick_reviewer(executor_model=os.environ.get("FORGE_MODEL"), available=<detected>, allowed_clis=config.allowed_clis)` → bound as `cli` (a `tools.cross_ai.detect.CLI` enum). No CLI on PATH → refuse with hint listing supported CLIs.
   - **Build prompt.** Call `tools.cross_ai.prompt.build_prompt(target, feature_id, repo_root)` → bound as `prompt`. The returned `Prompt` carries `diff_loc` (from `git diff --shortstat`) for `target=code`; `0` for `target=plan`.
   - **Redact.** Call `tools.redaction.filter(redaction.PromptPayload(text=prompt.body, files=prompt.files_referenced), tools.cross_ai.config.to_redaction_config(config.redaction))` → bound as `redaction_result`. The `to_redaction_config` adapter widens `RedactionRules` into `RedactionConfig` and unions user-supplied `deny_globs` with the redactor's secret-shaped defaults — never construct `RedactionConfig(...)` by hand. On `redaction_result.fatal_matches` non-empty: REFUSE dispatch, surface findings to user, abort cross-ai branch (in-house review continues normally).
   - **Build disclosure.** Call `tools.cross_ai.disclosure.build_disclosure(prompt, redaction_result, cli, config, diff_loc=prompt.diff_loc)` → bound as `disclosure`.
   - **Resolve dispatch mode (auto vs manual).** Pick exactly one branch below:
     - `--auto` flag passed → **auto mode**.
     - `config.mode == "auto"` → **auto mode**.
     - Otherwise → **manual mode**.
   - **When manual mode is selected:**
     1. **Write prompt to disk.** Call `tools.cross_ai.manual.write_prompt_to_disk(redaction_result.output_text, prompt.target, feature_id, repo_root)` → bound as `prompt_path`. **Always pass `redaction_result.output_text`, never `prompt.body`** — the on-disk file is what the operator pipes to the external CLI, so persisting the unredacted body would defeat the redaction step.
     2. **Render + display disclosure.** Call `tools.cross_ai.manual.format_disclosure_summary(disclosure, prompt_path)` and surface to user. The disclosure includes the cost-warn flag — when `disclosure.cost_warn_triggered`, print a callout but do NOT block (manual mode never gates on cost; user reviews before sending).
     3. **Halt cross-ai branch.** Skill exits the cross-ai branch; review phase remains in heavy-pass cycle N. Convergence frontmatter unchanged until paste-back via Step 4b. The in-house heavy pass (Step 5) still runs in the same cycle when its trigger conditions hold.
   - **When auto mode is selected:**
     1. **Hard caps refuse dispatch:** check `disclosure.prompt_tokens > config.max_prompt_tokens` → REFUSE; check `cli.value not in config.allowed_clis` → REFUSE. Note: the schema requires `allowed_clis` non-empty when `mode == "auto"`, so an empty tuple here means the config is structurally misconfigured — refuse with a hint that names the missing field. `pick_reviewer`'s "empty tuple means no filter" convention does NOT apply at this gate.
     2. **Dispatch-approval gate:** if `config.dispatch_approved_at` absent in `.forge/config.json`, surface disclosure + require user types `APPROVE` literal. On approval, call `tools.cross_ai.dispatch.record_dispatch_approval(repo_root)`. On refusal, abort.
     3. **Cost-warn gate:** if `disclosure.cost_warn_triggered` AND `--skip-cost-warn` flag NOT passed, require user types `APPROVE-COST` literal. When `--skip-cost-warn` is passed, prompt for rationale; empty rationale REFUSES; non-empty rationale appends a `decisions.md` deviation row of the shape `## YYYY-MM-DD — Cross-AI cost-warn skipped` with Context + Estimated cost + Rationale + Reviewer.
     4. **Dispatch:** call `tools.cross_ai.dispatch.auto_dispatch(cli=cli.value, prompt_text=redaction_result.output_text, timeout_seconds=config.timeout_seconds, retry=config.retry)` → bound as `result`.
     5. **On `DispatchError` (subprocess.TimeoutExpired / CalledProcessError / OSError):** log deviation in `decisions.md` (`cross-ai-auto-{timeout|failed|oserror}`); fall back to manual mode — call `prompt_path = tools.cross_ai.manual.write_prompt_to_disk(redaction_result.output_text, prompt.target, feature_id, repo_root)` then surface run instructions via `tools.cross_ai.manual.format_disclosure_summary(disclosure, prompt_path)`. **Halt cross-ai branch** (the operator finishes by hand via `--cross-ai-paste`); convergence frontmatter unchanged until paste-back.
     6. **On success:** write captured response to disk via `tools.cross_ai.dispatch.write_response_to_disk(result.response_text, feature_id, target, repo_root)`. Parse via `tools.cross_ai.parse.parse_response(result.response_text, reviewer_id=cli.value, target=target)` → bound as `findings`. Merge via `tools.cross_ai.manual.merge_findings_into_review(findings, target, feature_id, repo_root)`. Surface count appended. **Re-enter convergence loop:** bump `REVIEW.<target>.md` frontmatter `cycles: N+1` when HIGH+ findings were merged, then re-execute Step 6 (Convergence Log) + Step 7 (Drive convergence) against the merged file — mirrors the paste-back path in Step 4b. The auto success branch does NOT halt; control returns to the convergence driver.
4b. **Cross-AI paste-back (optional).** Triggered when user passes `--cross-ai-paste <path>` to `/forge:review`.
   - **Read response.** Call `tools.cross_ai.manual.read_paste_response(path)`. UTF-8 decode failure → surface error, abort.
   - **Resolve reviewer ID.** Call `tools.cross_ai.manual.extract_reviewer_id(response_text)`; on `None`, fall back to the `--reviewer <name>` flag if passed; else `"unknown"`. The helper parses the optional YAML frontmatter (`---\nreviewer: <id>\n---`) so the skill never has to inline a YAML parser.
   - **Parse findings.** Call `tools.cross_ai.parse.parse_response(response_text, reviewer_id, target)`. On `ParseWarning` (no table found): surface warning, prompt user to retry; do not merge.
   - **Merge findings.** Call `tools.cross_ai.manual.merge_findings_into_review(findings, target, feature_id, repo_root)`. Surface the count appended.
   - **Re-enter convergence loop.** Bump `REVIEW.<target>.md` frontmatter `cycles: N+1` if HIGH+ findings were merged. Re-execute Step 6 (Convergence Log) + Step 7 (Drive convergence) against the merged file. External findings count toward HIGH+ tally identically to in-house findings.
5. **Cycle N — Heavy subagent pass.** Triggered when ANY of:
   - self-review surfaced ≥ 1 HIGH+ finding, OR
   - user explicitly requested `--heavy`, OR
   - **`target == "code"` AND `len(articles) > 0`** — closes the self-review skip gap (Open Scoping #13). Without this rule a Constitution violation that self-review misses would never get tagged, and the §5.3.9 ship gate would see nothing.

   Steps:
   - Dispatch ONE review subagent. Apply the `forge-context-budget` skill rules and `forge-subagent-dispatch` shape. The PreToolUse hook (`hooks/check_budget.py`) tolerates the optional `articles` budget field.
   - Budget: SPEC § Acceptance + Negative Requirements + UNDERSTANDING § Pre-Mortem; for target=code, also `git diff --stat` plus the touched files. The `articles` budget field carries the filtered Constitution articles serialized via `Article.to_budget_dict()` (Task 5).
   - Task: produce findings the self-pass missed. For target=code, additionally check the diff against every article in `articles`. **Tag every article-related finding** in the REVIEW.code.md row's Problem column with `[constitution:A<n>]` (matching the article's `id`). Severity mapping for article violations:
     - CRITICAL article → HIGH severity finding.
     - SHOULD article → MEDIUM severity finding.
     - MAY article → LOW severity finding.

     Every emitted row MUST include `Status: open` so the §5.3.9 ship gate can identify unresolved findings.

     Example finding row:

     | F-7 | HIGH | open | src/services/checkout.py:142 | [constitution:A1] direct ORM session call in service layer (Article 1 — Repository pattern) | move to `repository/checkout.py` | heavy-subagent |
   - Append findings to `REVIEW.<target>.md` with `Source: heavy-subagent`.
6. **Update Convergence Log row** for cycle N in `REVIEW.<target>.md`: findings opened, findings resolved (the planning agent or user resolved them), HIGH+ remaining.
7. **Drive convergence.**
   - When a finding is resolved (code or spec edit), update its row's `Status` from `open` to `resolved` in `REVIEW.<target>.md`.
   - When the user logs an exception in `decisions.md` referencing the finding id, update the row's `Status` to `accepted-risk`.
   - If HIGH+ remaining > 0 AND cycle N < 3: surface findings to user, accept resolutions (edits to SPEC / PLAN / code or accepted-risk entries in `decisions.md`), bump `REVIEW.<target>.md` frontmatter `cycles: N+1`, repeat steps 4–6.
   - If HIGH+ remaining > 0 AND cycle N == 3: keep `REVIEW.<target>.md` frontmatter `status: open`, surface to user with full residual list, halt without transitioning state. Document blocker in `decisions.md` § Open.
   - If HIGH+ remaining == 0: set `REVIEW.<target>.md` frontmatter `status: resolved`, proceed.
8. **Self-review gate:** `REVIEW.<target>.md` status is `resolved` AND no BLOCK findings remain unresolved.
9. **Record target completion.** Call `tools.state.complete_review_target(path, review_target=<plan|code>)` so `phases.review.targets_done` records this pass. Idempotent within the same target.
10. **Transition state — depends on target:**
   - **target=plan:** review phase stays `in_progress`; `targets_done == ["plan"]`. Do **not** call `complete_phase("review")` yet — the gate requires both targets done. The next phase command is `/forge:execute` (which still observes review as in_progress; `forge-execute` accepts that state).
   - **target=code:** both targets are now in `targets_done`. Call `tools.state.complete_phase(path, "review")` (the gate clears) followed by `tools.state.start_phase(path, "verify")`.
11. **Surface to user:** `REVIEW.<target>.md` path, findings count by severity, cycles used, and the resolved next phase (`/forge:execute` after target=plan; `/forge:verify` after target=code).

## Done

`REVIEW.<target>.md` exists, status is `resolved`, no BLOCK or HIGH findings remain unresolved. `phases.review.targets_done` records the just-completed target. After target=code only, `state.json` reflects review=done.

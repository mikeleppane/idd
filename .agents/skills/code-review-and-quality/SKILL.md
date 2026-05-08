---
name: code-review-and-quality
description: Code review skill for the FORGE plugin repo. Use whenever you are reviewing a change before merge — your own code, another agent's code, a teammate's PR — or when the user asks "is this ready?", "review this", "check this change", "look this over". Also invoke proactively after declaring a phase implementation done and before opening a PR. Reviewing AI-generated code is a stronger trigger, not a weaker one — false confidence is the dominant failure mode.
---

# Code Review & Quality (FORGE)

Multi-axis code review for the FORGE plugin repo. The output of this skill is a **structured Markdown review report** with findings grouped by severity, each carrying a `file:line` reference and a quoted snippet, followed by a clear verdict.

This skill is for the *review* moment. Three companion skills cover the *production* moments and they take precedence on the rules they own:

- [coding-guidance-python](../coding-guidance-python/SKILL.md) — Python implementation contract (sync only, no pydantic, no async stack, dataclass-for-boundaries, `pathlib.Path`-only, schema validation as functions, `subprocess` discipline, error handling, mypy-strict typing). When a finding is "this code violates the Python contract", *cite this skill* — don't restate its rules.
- [test-driven-development](../test-driven-development/SKILL.md) — failing test first, Prove-It pattern for bugs, behavioral assertions on `Path` payloads and dict outputs (not internals). When a finding is "this change shipped without TDD" or "this test asserts on internals", point at this skill.
- [git-conventions](../git-conventions/SKILL.md) — Conventional Commits with mandatory scope from the FORGE vocabulary, atomic commits, no `Co-Authored-By: Claude` trailers, Prove-It tests for bug fixes. When a finding is about commit hygiene, scope vocabulary, or PR shape, cite this.

**Spot-checks vs. rule restatement.** The five axes below contain triage prompts of the form "did you check X?". Those are deliberate — they name *what* to look for during the walk-through. They are not the rule itself. When you file a finding, cite the source skill rather than restating the rule content in the review; the source is authoritative and this skill cannot stay in sync with it forever.

Project-shape decisions (plugin layout, lifecycle phases, tier names, schema contracts) live in the planning specs under `docs/specs/` and the in-flight plan under `docs/plans/`. Those override anything in this skill.

---

## The approval standard

**Approve a change when it definitely improves overall code health, even if it isn't perfect.** Perfect code does not exist; the goal is continuous improvement. Don't block a change because it isn't exactly how you'd have written it. If it improves the codebase and follows the project's conventions, approve it — and file the cleanup ideas you spotted as `Suggestion` or as a separate issue.

The corollary: **don't rubber-stamp.** "LGTM" without evidence of actual review helps no one and trains the team to skip the review step. Every approval should be backed by something concrete — a test you ran, an axis you walked through, a deliberate "I checked X and Y, both clean."

---

## When to use this skill

- Before merging any PR or change.
- After completing a phase implementation, before declaring it done (e.g. "M2 Task 7 complete" → run the review before the next task starts).
- When another agent or model produced code you need to evaluate.
- After any bug fix — review both the fix and the Prove-It regression test.
- When a teammate asks "is this ready?", "look this over", "anything I missed?".

## When *not* to use it

- Trivial one-line changes (typo, import order, formatter churn) — the review is one line too. Skip the report format and just say "looks good" or "nope, X is wrong".
- A spec is missing and you don't yet know what the code is *supposed* to do — that's a spec problem, not a review problem. Reviewing code against an unstated intent produces opinion, not findings. For FORGE's own work, the SPEC.md / PLAN.md / phase task is the intent — if it isn't named, ask first.
- The change is part of an in-progress branch the author has explicitly marked as WIP. Wait until they're done.

---

## Severity vocabulary

Every finding gets a severity label. This is what makes a review actionable instead of an opinion blob.

| Label | Meaning | Author Action |
|---|---|---|
| `Critical` | Blocks merge. Will crash, corrupt data, leak secrets, break a stable public contract *without* a documented caller-migration path, violate `coding-guidance-python` "What this codebase does not use", or break the plugin's installable shape. | Must fix before merge. |
| `Important` | Should fix before merge. Bug on a less-likely path, breaks the FORGE lifecycle invariant (phase ordering, state transition rule), introduces an async / pydantic / `os.path` shape the codebase doesn't use, design issue that will compound. | Fix or explicitly defer with reason recorded. |
| `Suggestion` | Would improve the change. Refactor, clarification, missing test for an edge case. | Worth doing; reviewer doesn't block on it. |
| `Nit` | Optional polish. Naming, formatting (where the formatter doesn't already enforce), micro-style. | Author may ignore. Use sparingly — too many nits drown the real findings. |
| `FYI` | Informational. Context for future readers, related bug to file, observation. | No action needed. |

**Rule:** if a finding has no severity label, the author has to guess what's required. That makes the review unusable. Label every finding.

---

## The review process

Walk these five steps in order. Don't jump straight to "let me look at the code" — half the value of a review comes from steps 1 and 2.

### Step 1 — Understand the intent

Before reading code, find the answer to:

- What is this change trying to accomplish? (commit message, PR description, the active plan task in `docs/plans/<latest>.md`, the SPEC.md being implemented)
- What was the failing user-visible behavior, or what new behavior is being added?
- Which lifecycle invariants does it touch? (phase ordering in `tools/state.py`, schema contracts under `schemas/`, hook denial format in `hooks/check_budget.py`, plugin manifest at `.claude-plugin/plugin.json`)

If you can't answer these from the artifacts the author provided, the change description is incomplete — that's the first finding (`Important: change description doesn't say what it does`). A reviewer who has to reconstruct intent from the diff is a reviewer who will miss things.

### Step 2 — Read the tests first

Tests reveal intent, coverage, and the author's mental model. They also tell you whether you can trust the implementation walk-through.

- Do tests exist for the change? `test-driven-development` makes the failing-test-first the default; a feature commit without a test is a `Critical` finding citing that skill.
- Do they test *behavior* (what the code is supposed to do) rather than *implementation* (which functions get called)? Implementation tests calcify the code and offer a false sense of safety.
- Are edge cases covered (empty input, boundary values, error paths, malformed JSON, missing files, schema validation failures)?
- Do test names describe the scenario? `coding-guidance-python` and `test-driven-development` name `test_<unit>_<scenario>_<expected_behavior>` as the target style (e.g. `test_complete_phase_when_status_pending_raises`) because it reads as a sentence in failure output.
- Do tests assert on the **observable state of `Path` payloads and dict outputs**, not on internals? Asserting on `state["phases"]["spec"]["status"] == "done"` is right; asserting that a private helper was called is wrong.
- For bug fixes: does the test follow the **Prove-It pattern**? The test must fail against the pre-fix tree and pass against the post-fix tree. If the commit doesn't show the test was demonstrated against the broken code, file `Important`.
- Would the tests catch a regression if someone changed the implementation tomorrow? Mutation-test the test mentally — flip the implementation's return value or skip a branch, then ask: does the assertion catch it?

### Step 3 — Walk the implementation through the five axes

Hold all five axes in mind for each file. Don't do five passes — do one pass with five lenses. The axes are:

1. **Correctness** — does it do what it claims?
2. **Readability & simplicity** — can a future agent or human understand it cold?
3. **Architecture** — does it fit the system's shape and the plugin layout invariants?
4. **Security** — does it expose anything new?
5. **Performance** — does it introduce a real bottleneck?

Detail for each axis is below in *The five axes*.

### Step 4 — Categorize findings

For every finding, attach:

- **Severity** — `Critical` / `Important` / `Suggestion` / `Nit` / `FYI`
- **File and line** — `tools/state.py:142`
- **Snippet** — the actual code, copied verbatim. If you can't quote it, you don't have a finding.
- **Problem** — 1–3 sentences in plain English. What is wrong and why.
- **Fix** — concrete replacement code or, if the fix is conceptual, the end state.

A finding without a quoted snippet is suspicious — it usually means the reviewer is recalling a pattern instead of reading the diff. Don't trust your memory of the file; quote the line.

### Step 5 — Verify the verification story

Check what the author actually ran:

- Was `make check` clean? (Floor — runs `make lint` (ruff) + `make typecheck` (mypy strict) + `make test` (pytest). Documented in `Makefile` and the M1 plan.)
- Did `python -m tools.check_schemas` run when JSON Schemas were touched?
- Did `python -m tools.lint_frontmatter <files>` run when commands, skills, or templates were edited?
- For hook changes (`hooks/check_budget.py` or `hooks/hooks.json`): was the deny path actually exercised? "Bypass the forge-context-budget skill: dispatch with no budget block" should produce `permissionDecision: deny` with reason starting `FORGE context-budget hook:`. If the change to the hook didn't include a deny-path test, file `Important`.
- For plugin layout changes (`.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `commands/*.md`, `skills/<name>/SKILL.md`): was the plugin actually loaded via `claude --plugin-dir <repo>` and the affected command or skill manually invoked? `claude plugin validate .` is the recommended manual gate when available.
- For markdown surface (templates, skills, commands): does its frontmatter pass `tools.lint_frontmatter` against the right schema?

A green CI is necessary, not sufficient. Type-check passing is not the same as the feature working. The plugin loading correctly in Claude Code is part of the verification story for any markdown surface change.

---

## The five axes

Each axis lists FORGE-specific failure modes worth scanning for. These are *prompts*, not exhaustive checklists — the actual finding has to come from reading the code, not pattern-matching against the list.

**M2 reality check.** FORGE is mid-development: M1 (focused tier + foundation) is shipped; M2 (standard tier) is in flight; M3+ (constitution, adaptive routing, delta proposals, cross-AI review) are deferred. Apply each axis prompt with this discipline:

- **If the convention exists** in the project's stated rules (`coding-guidance-python`, `test-driven-development`, `git-conventions`, the active plan in `docs/plans/`) and the diff *uses or violates* it → file a finding citing the source.
- **If the diff is introducing the convention** for the first time → check it against the stated rule and file a finding if it diverges.
- **If the convention is absent from the codebase entirely** → do not file `Critical` for "missing X" when X belongs to a deferred milestone (e.g., demanding a Constitution preflight in an M2 commit is misplaced — that arrives in M3+).

When in doubt, prefer to cite the companion skills rather than restate convention details inline — the source files own the rules and stay current.

### 1. Correctness — does the code do what it claims?

- Does it match the spec (SPEC.md), the active plan task (`docs/plans/<latest>.md`), or the commit description?
- Are edge cases handled — empty input, missing keys, `None`, boundary values, malformed JSON, missing files, dates that fail RFC-3339 format check?
- Are error paths handled, not just the happy path? FORGE's tooling raises domain `RuntimeError` subclasses (`StateError`, `ArchiveError`) — does the new code do the same per `coding-guidance-python` "Domain errors are named `RuntimeError` subclasses"?
- Off-by-one errors, state-transition gaps, mutation hidden in `read_*` / `parse_*` names? `tools/state.py` is the canonical example: `read_state` is read-only; `start_phase` / `complete_phase` mutate and persist. A new helper named `read_*` that mutates is a `Critical` shape violation.
- Exception context preserved with `raise NewError(...) from original` per `coding-guidance-python` "First-tier bug-causers"?
- Specific exception types caught, not bare `except Exception` outside the CLI entry-point boundary?
- For state machine changes: does the new transition respect lifecycle ordering? `complete_phase` requires `current_phase == phase` AND status `in_progress`. A new caller that bypasses these checks is `Critical`.
- For schema validators: does the validator actually call `iter_errors` and surface `path` plus `message`, or does it short-circuit on the first failure and lose context?
- For new tests: do they actually fail when the code is broken? Mutation-test the test mentally — if you flipped the implementation, would the assertion catch it?

### 2. Readability & simplicity — can it be understood without explanation?

- Are names descriptive and consistent with surrounding files? (No bare `temp`, `data`, `result` without context. State payloads stay `payload` for consistency with `tools/state.py`.)
- Is control flow straightforward? Early returns over deep nesting (>3 levels is the line per `coding-guidance-python` "Decision heuristics").
- Function size — anything over ~40 lines is usually doing more than one thing (`coding-guidance-python` "Decision heuristics"). Extract a helper.
- Parameter count — anything over 5 meaningful parameters wants a `dataclass(frozen=True)` (the project's chosen seam — see `tools/bdd_detect.BDDFramework`) or a split.
- **Could this be done in fewer lines?** A 1000-line module where 100 would suffice is a failure. But: don't *force* compression for its own sake — clarity wins over brevity.
- **Are abstractions earning their complexity?** Don't generalize until the third use case. A `Protocol` with one implementation is premature. FORGE does not currently use `Protocol` + dependency injection (`coding-guidance-python` "What this codebase does not use") — introducing one needs a real second caller.
- Are the comments doing real work? Comments that restate what well-named code already says are noise (the global Claude Code rule rules out commented-out code; the same principle applies to commentary). Comments that explain a non-obvious *why* are valuable — keep them.
- Dead code artifacts — no-op variables, leftover backwards-compat shims, `# removed` comments, `_unused` renames? Per the global rule, delete completely; don't leave breadcrumbs.

### 3. Architecture — does it fit the system?

- **Plugin layout invariants.** `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` are the canonical Claude Code surface; skills live at `skills/<name>/SKILL.md`; commands are flat `commands/*.md`. A change that puts a skill at `skills/<name>.md` (file, not directory) or a command nested under a subfolder breaks the loader and is a `Critical` finding.
- **`tools/` is a flat utility package.** No subpackages today (`tools/state.py`, `tools/bdd_detect.py`, `tools/archive.py`, `tools/lint_frontmatter.py`, `tools/check_schemas.py`). A new module that introduces `tools/<subpkg>/__init__.py` needs a real reason; flag for discussion before approving.
- **Hooks have minimal dependencies.** `hooks/check_budget.py` runs as a `python3` process per `hooks/hooks.json`. It must run on a stock Python 3.12 with stdlib only — importing `jsonschema`, `yaml`, or anything from the `.venv` is a `Critical` finding (the hook process won't have them installed).
- **One-source-of-truth for lifecycle phases.** `tools/state.VALID_LIFECYCLE_PHASES` is the canonical tuple; `schemas/state.schema.json` mirrors it. Adding a new phase in one place but not the other is `Critical` — they will silently disagree.
- **Schema-vs-template parity.** Each new template under `templates/` should have a matching frontmatter schema under `schemas/`, and `tools.lint_frontmatter` should route the template basename to that schema. Three-way drift between template, schema, and lint router is the bug class to watch for.
- Does it follow existing patterns or invent a new one? If new, is it justified? (A new "Manager" / "Service" / "Handler" suffix where the surrounding code uses verb functions is a smell.)
- Are there shallow modules that should be deepened? (Interface nearly as complex as the implementation — usually the right move is to inline the helper into its single caller.)
- Is `dict[str, Any]` flowing through multiple layers? At a real boundary, define a `dataclass(frozen=True)` so the contract is named. (Inside one module on a state payload that the schema already constrains, `dict[str, Any]` is fine — that's the chosen pattern in `tools/state.py`.)

### 4. Security — does it expose anything new?

`coding-guidance-python` "First-tier bug-causers" owns the security rule set for FORGE's Python tooling. This skill does not restate it. During the review walk, hold these triage prompts in mind and *cite the source* when you file:

- Any **hardcoded secrets**, **`shell=True` in `subprocess`**, **`eval`/`exec`/`pickle` on untrusted data**, **SQL string concatenation**, or **`random` module used for security purposes**? File `Critical` citing `coding-guidance-python`. (Even though FORGE has no DB today, the principle holds: any future DB integration uses parameterized queries.)
- Any **`subprocess` call without `check=True`** or using string form instead of list form? `coding-guidance-python` "What this codebase does not use" rules out `shell=True`; ruff `S` series enforces it. File `Important`.
- Any **`os.path`** instead of `pathlib.Path`? Path math via string concatenation is the gateway to traversal bugs. File `Important`, citing `coding-guidance-python` "`pathlib.Path` only".
- Any **path computed from user-controlled input without validation**? `tools/archive.py` validates capability slugs and feature ids with regex before joining them into paths — this is the pattern; new code that joins untrusted strings into `Path(...)` without a regex check is `Important` minimum.
- Any **schema validator that swallows errors and returns success**? Failing to surface `iter_errors` output is silent corruption. `Critical`.
- Any **internal exception type leaking across a public boundary**, forcing callers to know the implementation? `Important` — translate into a domain `RuntimeError` subclass (`StateError`, `ArchiveError`) per `coding-guidance-python` "Error handling".

If a security finding is well-known (those above), this skill expects you to cite `coding-guidance-python` rather than restate the rule. If it's a novel security concern not covered there, file the finding *and* surface it as a candidate addition to `coding-guidance-python` "First-tier bug-causers".

### 5. Performance — any bottleneck?

Performance findings only fire when the cost is *real and measurable*. "Could be slow" without numbers is speculation, not a finding.

FORGE's tooling is sync (`coding-guidance-python` "What this codebase does not use" — no async stack). The dominant cost shapes here are file I/O and process spawn, not async loop stalls.

- **Re-reading the same file inside a loop.** Schema files (`schemas/*.json`), templates, the `_meta.json`, intel files. If a hot loop opens the same path each iteration, hoist the read outside the loop. `Important` if the loop runs over feature folders or scenarios; `Suggestion` if it's a one-shot CLI tool.
- **Recompiling regex inside a function called per item.** `tools/archive._CAPABILITY_RE` is module-scope on purpose. New `re.compile(...)` inside a per-item function is a `Suggestion` minimum.
- **Eager full-file reads when only the frontmatter is needed.** `tools/lint_frontmatter` reads files in full because it lints the whole file; that's correct. A new helper that only needs the YAML block but reads megabytes of body is `Suggestion`.
- **`subprocess.run` per item where one batched call would do.** Spawning `git` once per commit when `git log --format=...` would yield the whole list. `Important` minimum.
- **Schema validator instantiated per call.** `jsonschema.Draft202012Validator(schema, format_checker=...)` is cheap to construct but not free; if a hot path constructs a fresh validator each iteration, hoist it. `Suggestion`.

If you can't say roughly *how much* slower the current code is than the proposed fix, demote the finding to `Suggestion` or `FYI`. Optimize after measurement.

---

## What makes a finding good (and what makes one bad)

The single most useful filter:

> **Would this finding cost the team something the next time they modify this code?**

If the answer is no, the finding is noise. Even if it's *technically correct*. The cost of a noisy review is high — authors learn to skim past findings, and real issues hide in the noise.

### Bad finding example 1 — technically correct, zero impact

```
Suggestion: Use list comprehension
tools/lint_frontmatter.py:88

  errors = []
  for entry in entries:
      errors.append(check(entry))

→ errors = [check(entry) for entry in entries]
```

This is technically correct. List comprehensions are idiomatic. But: the loop is clear, the surrounding file mixes both styles where readability calls for it, and "comprehension" doesn't prevent any future bug. The author will skim past, and one more `Suggestion` like this trains them to skim past the real ones. **Don't file it.**

### Bad finding example 2 — speculation stated as fact

```
Important: Possible race on state.json during concurrent /forge:execute
tools/state.py:142

complete_phase() reads then writes without an exclusive lock.
If two /forge:execute commands run for the same feature concurrently, this races.
```

Looks like a real finding — names a function, names a scenario, proposes a concern. But the reviewer hasn't shown that two `/forge:execute` commands ever run concurrently against the same feature folder in this codebase. The single-feature, single-session execution model means concurrency on one `state.json` is not a thing today. The whole finding rests on an unverified "if". **A hypothesis stated as a finding is a false positive.** Either trace the call sites and prove it, or don't file it.

### Bad finding example 3 — proposes inconsistency

```
Suggestion: Use match/case for the dispatch
tools/lint_frontmatter.py:55

The if/elif chain on file basename would be cleaner as a match statement.
```

In a file where every other dispatch is `if/elif`, proposing `match` introduces inconsistency for a small clarity gain. **Match the file's existing patterns.** If the user wants a project-wide migration to `match`, that's a different conversation, not a review nit.

### Bad finding example 4 — applies a public-API rule to a private function

```
Important: Missing Google-style docstring
tools/state.py:34

def _utc_now_iso() -> str:
    ...
```

`coding-guidance-python` "Google docstrings on the public surface" requires docstrings on the public API. `_utc_now_iso` is private (underscore prefix), the body is one line, and the name reads as a sentence. Filing this is misapplying the rule.

### Bad finding example 5 — demands a deferred-milestone pattern

```
Critical: No Constitution preflight before phase exit
commands/plan.md:14

The /forge:plan command does not invoke .forge/CONSTITUTION.md preflight before
calling start_phase(). This violates the design spec §6.7 "Phase entry gates".
```

Constitution preflight is M3+ work (per the active plan in `docs/plans/<latest>.md` and the handover decisions). Demanding it in an M2 commit fails the M2 reality check above. The right response is `FYI` at most, noting the milestone the work belongs to.

### Good finding example

Following the per-finding format defined in *Output format* below:

````markdown
**Critical: New helper imports `jsonschema` from inside the hook entry point**
`hooks/check_budget.py:12`

```python
import jsonschema
```

`hooks/check_budget.py` runs as a `python3` subprocess wired by `hooks/hooks.json`. The hook process does not see the project's `.venv`, so `jsonschema` is not importable there — Claude Code will surface a `ModuleNotFoundError` and the deny path will silently fail open. The hook contract is "stdlib only".

**Fix:** keep validation inside `hooks/check_budget.py` to plain-Python checks (string presence + YAML block parsing via stdlib regex or hand-rolled). If schema validation is needed, perform it in `tools/` ahead of the dispatch and pass the result through the dispatch payload.
````

Quoted snippet. Names a specific invariant. Explains the consequence. Points at the right place to do the work. **This is the shape every finding should aim for.**

---

## Budget

**The cap exists to suppress noise, not to suppress defects.**

- `Critical` and `Important` findings are **uncapped**. File every one. They are real defects; suppressing them defeats the purpose of the review.
- `Suggestion` + `Nit` combined are capped at **5–7 per review**. Past that, you're spending the author's attention on polish at the expense of the substance. Drop the marginal items or save them for a follow-up issue.
- `FYI` is uncapped but use it sparingly — every entry costs reading time.

**If a single change genuinely warrants more than ~5 Important findings, the change is too dense to merge safely.** The right response is not to drop findings, it is to file one top-level finding: *"this change is too large / too entangled to review properly — split it into reviewable slices"* — see `git-conventions`. Then address the splits in follow-up reviews.

---

## Strengths section

End the review with up to 3 short positive observations. One sentence each, ≤120 chars.

```
What's good
  • New BDDFramework dataclass uses frozen=True — locks the boundary contract
  • Tests cover the override path AND the false-positive lockfile path explicitly
  • Module-level regex compiled once — no per-call recompile in the hot path
```

This isn't sycophancy. Naming what works:

- **Reinforces patterns** the author should keep using.
- **Calibrates** the harshness of the rest of the review — a critical bug-find lands differently when the rest of the change is acknowledged as solid.
- **Helps future readers** of the review history understand what the codebase considers "good", which is useful when the companion skills don't yet cover it.

Skip the section if you genuinely have nothing positive — fabricated strengths read worse than none. But before you skip, look again — well-written tests, a good commit message, deleting more code than it adds, are all worth a line.

---

## Reviewing AI-generated code

This is the dominant case in FORGE right now. Treat it as a *stronger* trigger for this skill, not a weaker one. The failure modes:

- **False confidence.** AI-generated code reads as authoritative. It uses the right vocabulary, follows the right shape, looks plausible. *Plausible-looking code that's subtly wrong is the dominant defect.*
- **Pattern transplant.** The model may import a pattern from another codebase that doesn't match FORGE's conventions — `pydantic.BaseModel` where FORGE uses `dataclass(frozen=True)` + JSON Schema; `asyncio` where FORGE is sync; `os.path.join` where FORGE uses `pathlib`; `requests` where FORGE uses stdlib only inside hooks; `loguru` where FORGE prints from CLI tools and asserts in tests. Each is `Critical` per `coding-guidance-python` "What this codebase does not use".
- **Hallucinated APIs.** Method names that look right but don't exist, kwargs that the library doesn't accept, fields the schema doesn't have. Always verify import paths and method signatures against the actual file. For external libraries (`jsonschema`, `PyYAML`), Context7 MCP is the right tool to verify current syntax.
- **Hallucinated paths.** A finding that references `tools/foo.py` when the file is `tools/foo_helpers.py` is a sign the agent is recalling a pattern, not reading the diff. Re-grep before sending.
- **Test theater.** Tests that assert what the implementation *does*, not what the contract *should* be. They pass, prove nothing, and lock the implementation in place. `test-driven-development` rules out asserting on internals; cite it.
- **Sycophancy in the change description.** Claims the change "improves performance", "adds robustness", "follows best practices" without evidence. The diff is the evidence; if the description doesn't match the diff, distrust the description.
- **Extra-mile features.** Code that solves more than was asked. Unused options parameters, premature config flags, defensive code for impossible inputs. Pull these out — they're future maintenance cost for no current benefit. The Claude Code system rule "don't add features beyond what the task requires" applies here.

When the author *is* an AI agent, you have a special obligation: nobody else is going to push back. **Be more direct, not less.** Polite hedging — "this might be worth considering" — gets the wrong things merged. State problems plainly, with evidence, and ask for the fix.

---

## Change sizing & splitting

| Size | Verdict |
|---|---|
| ~100 lines | Easy to review in one sitting. Aim for this. |
| ~300 lines | Acceptable for a single logical change (e.g. one M2 plan task). |
| ~1000+ lines | Too large. Push back: ask the author to split. |

If a change is too large, the right *first* finding is: "this needs to be split before review", and the rest of the review can wait. Trying to review a 1500-line change properly is the path to LGTM-ing real bugs.

Splitting strategies (from `git-conventions`):

| Strategy | When |
|---|---|
| **Stack** | Submit a small change, base the next on it. Sequential dependencies. |
| **By file group** | Different concerns separated. (Schemas in one commit, the consuming tool in the next.) |
| **Horizontal** | Shared types / templates first, consumers next. Layered changes. |
| **Vertical** | Smaller end-to-end slices of the feature. (One M2 task at a time, not three at once.) |

**Separate refactors from feature work.** A change that refactors *and* adds new behavior is two changes — file `Important: split refactor from feature` and ask for it.

---

## Dependency review

`coding-guidance-python` "What this codebase does not use" treats new runtime dependencies as a high-friction decision. When a change adds anything to `pyproject.toml [project] dependencies` or `[project.optional-dependencies] dev`, file the boundary check explicitly:

- `Important: New runtime dependency added — was this discussed?` Even if the answer is yes, the answer needs to appear in the PR description.
- Is the dependency on the "does not use" list (`pydantic`, async stack, structured-logging frameworks, data-validation libs that wrap JSON Schema)? `Critical` if so — the rule is explicit.
- Is it actively maintained? Last release date, open-issue count.
- License compatible with MIT (the FORGE plugin license)?
- Does the existing stack solve this? (`jsonschema` covers schema validation; `PyYAML` covers YAML; `tomllib` (stdlib) covers TOML reads; `re` covers slug validation. The answer is almost always *use what's already here*.)
- For hook dependencies: hooks must run on stdlib only. A dep that the hook directly or transitively imports is `Critical` regardless of how nice it is.

Every dependency is a liability. Prefer stdlib + the existing four (`jsonschema`, `PyYAML`, `pytest`, `ruff`) before reaching for a new one.

---

## Dead code hygiene

Refactors and feature changes often leave orphaned code. After the implementation walk-through, scan for:

- Functions and helpers no longer called.
- Schema fields no longer referenced by any validator caller.
- Templates no longer copied by any skill.
- Test fixtures no longer used.
- Constants whose only callers are deleted.

**Don't silently delete.** What looks orphaned to you may be in-progress work the author hasn't wired up yet, or part of a planned next slice (M2 has 17 tasks; a helper added in T3 may not have a consumer until T6). File a finding listing what *appears* unused and ask:

```
FYI: Apparent dead code after this change

  - parse_legacy_state() in tools/state.py — no remaining callers
  - templates/feature/old_PLAN.md — referenced nowhere in the plan
  - test_legacy_format() in tests/tools/test_state.py — covers a deleted code path

Is removing these in scope for this change, or part of a follow-up M2 task?
```

This both prevents missed cleanup and respects scope discipline (the global rule "don't add features beyond what the task requires" cuts both ways — it also rules out unsolicited deletion).

---

## Output format — the review report

Render the review as Markdown using this exact template. Stable structure means the author can scan it predictably and the report copy-pastes cleanly into a PR comment.

````markdown
# Review — <PR title or short change description>

## Summary

<2–4 sentences: what the change does, what state it's in, headline verdict.>

## Verdict

<one of: ✅ Approve / 🟡 Approve with suggestions / 🔴 Request changes / ⏸ Needs split>

<one-line rationale>

## Findings

### Critical

<each finding as: severity + title, then file:line, snippet block, problem, fix.
Omit the section heading if there are no findings at this severity.>

### Important

...

### Suggestion

...

### Nit

...

### FYI

...

## What's good

- <0–3 short positive observations, one line each>

## Verification

- [ ] Failing test added before the implementation per `test-driven-development`
- [ ] `make check` passes (ruff strict + mypy strict + pytest)
- [ ] `python -m tools.check_schemas` run if schemas were touched
- [ ] `python -m tools.lint_frontmatter <files>` run if commands / skills / templates were touched
- [ ] Hook deny path exercised if `hooks/` was touched
- [ ] Plugin manually loaded via `claude --plugin-dir .` if plugin manifest or surface markdown was touched
- [ ] Commit message follows Conventional Commits with allowed scope per `git-conventions`; no `Co-Authored-By: Claude` trailer
````

### Verdict ↔ severity mapping

This is a guide for the reviewer when picking a verdict — it is *not* part of the rendered report. Apply mechanically; the verdict should follow from the findings, not from a judgment call.

| Verdict | Allowed when |
|---|---|
| ✅ Approve | Zero `Critical` and zero `Important` findings. May have any number of `Suggestion` / `Nit` / `FYI`. |
| 🟡 Approve with suggestions | Same as Approve. Use this variant when there are non-blocking findings worth surfacing but the change is mergeable as-is. |
| 🔴 Request changes | At least one `Critical` *or* `Important` finding. Author must fix or explicitly defer (with reason recorded) before merge. |
| ⏸ Needs split | The change is too large or too entangled to review safely. Re-review after the split. |

If the verdict says Approve and the report contains an unresolved `Important`, the verdict is wrong — fix one or the other before sending.

### Per-finding format

Each finding follows this structure. The outer block uses **4-backtick** fences so the inner ` ```python ` blocks render correctly inside it; copy this same shape if you reproduce the format elsewhere.

`````markdown
**Critical: <short title>**
`tools/state.py:142`

```python
def complete_phase(path, phase):
    payload = read_state(path)
    payload["phases"][phase]["status"] = "done"
    write_state(path, payload)
```

<1–3 sentence problem statement explaining what is wrong and why it matters.>

**Fix:**

```python
def complete_phase(path: Path, phase: str, schema_path: Path | None = None) -> dict[str, Any]:
    if phase not in VALID_LIFECYCLE_PHASES:
        raise StateError(f"unknown phase '{phase}'")
    payload = read_state(path, schema_path=schema_path)
    if payload.get("current_phase") != phase:
        raise StateError(...)
    ...
```
`````

If you can't quote the line, you don't have a finding. If you can't write a concrete fix, the finding is too vague — sharpen it or drop it.

---

## Pre-send checklist

Before delivering the report, sanity-check it against this list. Each item is something a bad review gets wrong. Once authors learn to distrust your reviews, they stop reading them — keep the bar high.

**Findings:**

- [ ] Every finding has a severity label.
- [ ] Every finding quotes a specific file, line, and snippet from the actual diff.
- [ ] No finding is speculation ("might race if..." / "could possibly...") — each is grounded in the code.
- [ ] No finding fails the future-change filter (would this cost the team next time?).
- [ ] No finding proposes inconsistency with the surrounding file's patterns.
- [ ] No finding is restated under two different axes — pick one place to file it.
- [ ] No finding fires `Critical` for a deferred-milestone pattern (Constitution, `/forge:do`, delta proposals, cross-AI review are M3+/M4).

**Coverage:**

- [ ] Lifecycle invariants checked: phase ordering in `tools/state.py`, schema/template parity, hook stdlib-only rule, plugin layout invariants.
- [ ] Schema-vs-template-vs-lint-router three-way drift checked when a template or schema was touched.
- [ ] `subprocess` usage audited for `shell=True`, list form, `check=True`.
- [ ] `pathlib` over `os.path`; no path math via string concatenation on user input.
- [ ] Verification story is checked, not assumed (`make check`, `check_schemas`, `lint_frontmatter`, hook deny path, plugin load).

**Shape:**

- [ ] `Critical` findings appear at the top of Findings, not buried in a long Suggestions section.
- [ ] `Suggestion` + `Nit` combined ≤ 7. (`Critical` and `Important` are uncapped.)
- [ ] Strengths section reflects something genuinely worth naming, or is omitted (don't fabricate).
- [ ] An empty "What's good" section paired with a long Critical/Important list is a prompt to re-read for fairness before sending — possibly accurate, but check.
- [ ] The verdict matches the findings — `Approve` doesn't co-exist with any unaddressed `Critical` or `Important`.
- [ ] Approval is backed by something concrete (axes walked, verification confirmed) — never just "LGTM".

---

## Common rationalizations

The thoughts that lead to a bad review. Notice them, reverse course.

| Rationalization | Reality |
|---|---|
| "It works, that's good enough" | Working code that is unreadable, insecure, or architecturally wrong creates debt that compounds. The review is the gate. |
| "Tests pass, so it's good" | Tests are necessary, not sufficient. They don't catch architecture problems, security issues, or readability problems. |
| "The author probably already thought about this" | If you can't see the answer in the code or the description, neither can the next reader. Ask. |
| "I wrote this, so it's correct" | Authors are blind to their own assumptions. Self-review is necessary; it is not a substitute for another set of eyes. |
| "AI wrote this, it's probably fine" | AI-generated code needs *more* scrutiny, not less — it's confident and plausible even when wrong. |
| "I'll soften this — they might take it personally" | Sycophancy in reviews is a failure mode. Be respectful, name the code (not the person), but say what is true. |
| "I'll add a Suggestion for everything I noticed" | A 30-finding review gets ignored. Cap to 5–7 important ones; surface the rest as a follow-up issue if they matter. |
| "I'll approve and they can fix it later" | Later rarely comes. If it needs fixing, request changes. If it doesn't, drop the finding. |
| "It's almost the same as how I'd write it, so close enough" | "Definitely improves overall code health" is the bar, not "matches my style". Approve. |
| "This change is too big to review properly, so I'll skim it" | Skimming a 1500-line change is how real bugs reach main. Push back: split it. |

---

## Anti-patterns in FORGE reviews

Things that have been wrong in the past or are easy to get wrong here. Add to this list when a real review miss happens.

- **Letting a state-machine change land without checking lifecycle ordering** — `complete_phase` requires `current_phase == phase` AND status `in_progress`. A new transition that bypasses one check usually breaks the other in subtle ways.
- **Approving a new template without checking schema parity** — every `templates/feature/<X>.md` should have a matching `schemas/<x>-frontmatter.schema.json` AND a route in `tools.lint_frontmatter`. Three-way drift is silent until a real spec hits the linter.
- **Approving a hook change that imports anything beyond stdlib** — the hook subprocess won't have the venv, so the deny path silently no-ops. Always verify the hook still runs as `python3 hooks/check_budget.py` from a clean shell.
- **Approving a `pydantic` import** — `coding-guidance-python` "What this codebase does not use" rules it out. The fix is `dataclass(frozen=True)` + a JSON Schema if the boundary needs validation.
- **Approving an `asyncio` / `await` introduction** — FORGE is sync. Same skill, same rule.
- **Approving a `subprocess` call without `check=True` or with string-form `cmd=`** — ruff `S` rules will reject the latter; the former is a defect class.
- **Approving a path computed from user-controlled input** without a regex validation step — `tools/archive.py` is the canonical pattern; new code that joins untrusted strings into `Path(...)` without `_FEATURE_ID_RE`-style guard is path-traversal-prone.
- **Letting a commit through with `Co-Authored-By: Claude` trailer** — `git-conventions` rules it out; this is a hard repo policy.
- **Approving a frontmatter change without running `tools.lint_frontmatter`** — the schema-quality bar (description specificity, allowed fields) catches issues CI will fail on minutes later.
- **Approving a `.claude-plugin/plugin.json` or `marketplace.json` change without manually loading the plugin** in Claude Code — manifest typos are silent until install time.
- **Filing `Critical` for a missing M3+ pattern** (Constitution preflight, delta proposals, `/forge-validate`, cross-AI review) — the M2 reality check rules these out as scope creep, not as defects.

---

## Examples

**Good review shape:**

````markdown
# Review — feat(tools): add bdd_detect for ecosystem-aware Gherkin escalation

## Summary

Adds `tools/bdd_detect.py` with a single `detect(repo_root) -> BDDFramework | None` function and a frozen dataclass return type. Encodes design §6.6 detection rules for Python (pytest-bdd), Node (cucumber-js), Ruby (cucumber), and Go (godog). Ships 9 parametrized tests including the false-positive lockfile case. ~120 lines + ~80 test lines, single concern.

## Verdict

🟡 Approve with suggestions — change is mergeable; one non-blocking suggestion worth folding in.

## Findings

### Suggestion

**Suggestion: Cache the `.forge/config.json` read across calls in a single CLI invocation**
`tools/bdd_detect.py:34`

```python
def _read_forge_config_override(repo_root: Path) -> BDDFramework | None:
    config_path = repo_root / ".forge" / "config.json"
    if not config_path.is_file():
        return None
    ...
```

`detect()` is called from at least two skills (`forge-scenarios`, `forge-verify`) that may both run within one feature pass. If the override path is taken, the same JSON is read twice. A module-level `@functools.lru_cache(maxsize=8)` keyed on `repo_root` would amortize this for free.

Not a defect — current latency is fine for one-shot CLI calls; this is a forward-proofing call once skills start chaining.

## What's good

- `BDDFramework` is `dataclass(frozen=True)` — locks the boundary contract per `coding-guidance-python`
- False-positive lockfile case has its own test (`test_transitive_dep_in_lockfile_does_not_trigger`)
- Detection order (config override > python > node > ruby > go) is documented in the docstring AND mirrored in the test parametrization

## Verification

- [x] Failing tests written before the implementation
- [x] `make check` passes (ruff strict + mypy strict + pytest)
- [x] `python -m tools.check_schemas` not needed (no schema changes)
- [x] `python -m tools.lint_frontmatter` not needed (no markdown surface changes)
- [x] Hook deny path not affected (no hook changes)
- [x] Plugin load not needed (no manifest changes)
- [x] Commit message: `feat(tools): add bdd_detect for ecosystem-aware Gherkin escalation` — Conventional Commits, allowed scope, no Co-Authored-By trailer
````

**Bad review shape (drop these patterns):**

```
LGTM 👍

A few small things:
- could maybe consider using match here
- function is a bit long
- might be a race condition somewhere
- nit: prefer single quotes
```

No severity, no file:line, no snippet, speculation, contradicts the project's double-quote rule (ruff format default), and the LGTM up top makes the comments meaningless.

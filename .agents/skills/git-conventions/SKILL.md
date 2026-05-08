---
name: git-conventions
description: Structure every git commit in the FORGE repo with Conventional Commits and a mandatory scope, atomic commits, imperative-mood subjects, and Prove-It tests for bug fixes. Use when staging changes, writing commit messages, opening PRs, or resolving merge conflicts. Never add Co-Authored-By Claude trailers — even for AI-assisted work.
---

# Git Workflow & Commit Conventions (FORGE)

> Format: Conventional Commits | Scope: required | Breaking changes: `!` + footer
> Atomic commits, imperative mood, explain the *why* in the body when it isn't obvious from the diff.

A clean git history is a durable asset. Six months from now, `git log` and `git blame` are the first things anyone reads when diagnosing a regression, tracing a decision, or onboarding. Follow these conventions on *every* commit so history stays useful.

This skill is the **general-purpose git contract** for the FORGE repo. Test discipline (Prove-It for bug fixes) lives in [`../test-driven-development/SKILL.md`](../test-driven-development/SKILL.md). Python style and structure live in [`../coding-guidance-python/SKILL.md`](../coding-guidance-python/SKILL.md).

---

## Commit as Save Point

Treat commits as save points, branches as sandboxes, and history as documentation. With AI agents generating code fast, disciplined version control is what keeps changes manageable, reviewable, and reversible.

**Working pattern:** `Implement slice → make check → verify → commit → next slice`. Each green increment gets its own commit. If the next change breaks something, `git reset --hard HEAD` takes you back to the last green state — you never lose more than one increment.

---

## Commit Message Format

```
<type>(<scope>): <subject>

<body>

<footer>
```

All three parts carry weight: the subject catches attention, the body records motivation, the footer preserves breaking-change migrations and references.

---

## Subject Line

```
feat(tools): split state transitions into start/complete/finish
```

- **Target ≤72 characters** (≤50 ideal); **soft cap 90 chars** for unusual cases like spec-section anchors (e.g. `(§5.3.9)`). Don't sacrifice clarity for length. **Lowercase** after the colon. **Imperative mood** ("add", not "added"). **No trailing period.**
- **Be specific** — describe *what changed*, not *what you did*. `validate state.json with format-aware schema check` beats `update validator`.

---

## Types

Conventional Commits, no extras:

| Type | When to use |
|------|-------------|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `refactor` | Restructure with no behavior change |
| `perf` | Measurable performance improvement |
| `docs` | Documentation only — README, AGENTS.md, plan/spec docs |
| `test` | Test-only change |
| `build` | Build system, dependencies, tooling config |
| `ci` | CI/CD config (`.github/workflows/`) |
| `chore` | Maintenance — Makefile churn, repo scaffolding |
| `style` | Whitespace/formatting only |

Behavior changed? → `feat` or `fix`. Same behavior, different shape? → `refactor`. Per atomic-commit rule, code + its test usually ship as one `feat` or `fix` — no separate `test(...)` commit chasing it.

---

## Scopes (Required)

Every commit carries a scope identifying the area touched. Scopes are short, lowercase, and consistent.

| Scope | Covers |
|-------|--------|
| `repo` | Root-level scaffolding (`.gitignore`, `LICENSE`) |
| `readme` | README content |
| `plugin` | `.claude-plugin/plugin.json`, plugin loader concerns |
| `portability` | `AGENTS.md` and any non-Claude tooling discovery |
| `tools` | Anything under `tools/` (Python: state machine, frontmatter linter, schema checker) |
| `schema` | Anything under `schemas/` |
| `templates` | Anything under `templates/` |
| `skills` | Anything under `skills/` |
| `commands` | Anything under `commands/` |
| `hooks` | Anything under `hooks/` |
| `tests` | Test-only additions outside the atomic-commit case |
| `fixtures` | Anything under `tests/fixtures/` |
| `make` | `Makefile` |
| `ci` | `.github/workflows/*` |
| `docs` | Anything under `docs/` (currently gitignored — internal-only) |

Scopes evolve as the project grows. Add a new scope when a new module emerges that doesn't fit. Keep the list short — if half the commits land under one scope, split it.

**Cross-cutting changes:** pick the scope of the *primary* change. Secondary nudges in adjacent areas can ride along; whole-repo sweeps belong in their own commits.

---

## Body

The body explains **why** this change exists. The diff shows what.

```
feat(tools): enforce imperative + 'Use when' description quality bar

The frontmatter linter was accepting descriptions that started with
nouns ("Description of...") or skipped the trigger phrase entirely,
which left agents with weak invocation signals at runtime.

Require an imperative first word and a "Use when" clause. Failing
descriptions emit a single, actionable error pointing at the file.
```

- Wrap at **~72 chars**. Blank line between subject and body.
- First paragraph: problem or motivation. Optional second paragraph: approach, tradeoffs, rejected alternatives.
- Skip the body only when the subject is fully self-explanatory (typo, dep bump, formatter churn). If tempted to skip on anything bigger, you're underestimating the future reader.

---

## Breaking Changes

For changes that break a published contract — schema field renames, plugin manifest format shifts, CLI flag removals, importable-API breaks:

1. **Add `!` after the scope** — for scanning `git log`.
2. **Add a `BREAKING CHANGE:` footer** — for migration details.

```
feat(schema)!: rename state.status to state.phase

Aligns the field name with the SPEC.md frontmatter and removes the
overload of "status" used elsewhere in the plugin.

BREAKING CHANGE: existing state.json files written by older versions
must be migrated. Run `forge-tools migrate-state` (or rename the field
manually) before invoking the state machine.
```

Both the `!` and the footer are required.

---

## Atomic Commits

Each commit should be a single logical change that compiles, passes `make check`, and tells one coherent story on its own. If you're writing "and" in the subject, split.

**Good — one concern per commit:**

```
feat(tools): add tools.state.read_state with error handling
feat(tools): split state transitions into start/complete/finish
feat(tools): validate state.json with format-aware schema check
```

**Bad — bundled concerns:**

```
feat(tools): add state reader, split transitions, and add schema checks
```

Atomic commits make `git bisect` usable, `git revert` safe, and review tractable.

**Keep concerns separate.** Don't mix formatter churn with behavior changes. Don't combine refactors with features. Trivial cleanups (one rename, one import sort) can ride along; anything larger gets its own commit.

**Bug-fix commits include a Prove-It test.** A failing test that exercises the bug, plus the fix, in one commit. See [`../test-driven-development/SKILL.md`](../test-driven-development/SKILL.md). A `fix(...)` with no accompanying test is a smell.

---

## Change Size

| Size | Verdict |
|------|---------|
| ~100 lines | Easy to review, easy to revert — aim for this |
| ~300 lines | Acceptable for a single logical change |
| ~1000+ lines | Split before submitting, not after |

Large changes are harder to review, riskier to deploy, and harder to revert. If a single change exceeds ~1000 lines, it is almost always two or more changes wearing a trench coat — split it into reviewable slices that each stand on their own.

---

## No `Co-Authored-By: Claude` Trailers

**Repo policy. Not negotiable.**

Do **not** add a `Co-Authored-By: Claude <noreply@anthropic.com>` trailer (or any variant — `Claude Opus`, `Claude Code`, `claude-code`) to commits in this repo, **even when the commit was AI-assisted**. The maintainer has explicitly forbidden these trailers.

Applies to direct agent commits, user commits made after pairing with an agent, squash-merge messages, and amended/rebased/cherry-picked messages alike. If tooling adds the trailer automatically, strip it before the commit lands. If a hook complains, fix the hook — don't smuggle the trailer in.

The commit log records what was done and why. Authorship metadata for the AI is noise.

---

## Quality Gate Before Commit

The verification floor before declaring a change done:

```bash
make check       # ruff + mypy + pytest in one shot
```

Or run the parts individually:

```bash
make lint        # ruff check
make typecheck   # mypy
make test        # pytest
make fmt         # ruff format (write)
make fix         # ruff check --fix + ruff format
```

Never commit code that fails `make check`. Never use `--no-verify` to bypass a hook — if a hook is wrong, fix the hook.

---

## Commit the *Why*, Not the *What*

The diff shows exactly what changed. The commit message should answer: *"six months from now, when someone reads `git blame` on this line, what context will they need?"*

Bad: "updated the linter."
Good: "enforce imperative + 'Use when' description quality bar."

---

## Things to Avoid

- **Vague subjects** — "fix bug", "update code", "WIP", "misc".
- **Process narration** — "as discussed", "per review feedback", "Claude suggested".
- **Temporal language** — "now we do X", "previously Y". Write in steady-state tense.
- **Implementation narration** — "first I changed X, then Y".
- **Emoji** in commit messages.
- **Ticket numbers in the subject** — put them in the footer: `Refs: #42`.
- **`Co-Authored-By: Claude` trailers** — see policy above.
- **`--amend` on already-pushed commits.**
- **`--no-verify`** — if a hook is wrong, fix the hook.
- **Force-push to `main`** — never. Force-push your own feature branch only if you understand the consequences.

---

## Footer

Blank line between body and footer. Only include footers when they carry information.

```
BREAKING CHANGE: <description and migration path>
Closes: #789
Refs: #123, #456
```

---

## Commit Workflow (FORGE)

1. **Review staged changes** — `git diff --staged`. Does the diff match a single logical change? If not, split.
2. **Pre-commit hygiene:**

   ```bash
   git diff --staged
   git diff --staged | grep -iE "password|secret|api[_-]?key|token|bearer"
   make check
   ```

3. **Choose the right type and scope.**
4. **Write a specific subject in imperative mood.**
5. **Write the body** — why does this change exist?
6. **Check for breaking changes** — `!` + `BREAKING CHANGE:` footer.
7. **Confirm no `Co-Authored-By: Claude` trailer is present.**

---

## Branch and PR Hygiene

- **Default branch:** `main`. Feature work happens on branches.
- **Branch name:** `feat/<scope>-<short-description>` — e.g. `feat/m1-foundation`, `fix/state-machine-race`. Short, hyphenated, type-prefixed.
- **Keep branches short-lived** — aim to merge within 1–3 days. Long-lived branches accumulate merge risk.
- **Rebase vs merge:** prefer rebase to keep your local branch in sync with `main`; merge via PR.
- **Force-push:** only on your own feature branch, never on `main`.
- **Delete branches after merge.**
- **PR title:** mirror a commit subject — `type(scope): imperative subject`.
- **PR description:** mirror a commit body — problem, approach, anything a reviewer needs that the diff won't show.

---

## Recovery and Correction

- **Wrong subject on last commit, not pushed:** `git commit --amend`.
- **Wrong file staged:** `git restore --staged <file>`.
- **Accidental commit on main:** branch from HEAD, reset main to upstream, push the branch. *Do not* force-push main.
- **Discarded work:** `git reflog` — survives ~90 days.

When recovery is ambiguous, stop and ask before running destructive commands (`reset --hard`, `push --force`, `clean -f`).

---

## Conflict Resolution

When `git rebase` or `git merge` halts on a conflict:

1. **Read both sides** — understand what each commit was trying to do before picking.
2. **Resolve**, then `git add <file>` and `git rebase --continue` (or `git merge --continue`).
3. **Run `make check` after every resolved conflict.** Conflicts that compile can still be semantically wrong.
4. **Never resolve a conflict by deleting tests** unless the test itself was the cause and the removal is explained in the commit message.
5. **If a rebase gets out of hand**, `git rebase --abort` and reconsider. A merge commit beats a corrupted rebase.

---

## Git for Debugging

Atomic commits with descriptive messages are what make `git bisect`, `git blame`, and `git log --grep` effective. A history of "fix stuff" commits makes them useless. Useful commands:

```bash
git bisect start && git bisect bad HEAD && git bisect good <commit>
git log --oneline -20 -- tools/
git blame -L <start>,<end> tools/forge_tools/state.py
git log --grep="frontmatter" --oneline
git reflog   # discarded-work recovery, ~90 days
```

---

## Anti-Patterns

| Rationalization | Reality |
|-----------------|---------|
| "I'll commit when the feature is done" | One giant commit is impossible to review, debug, or revert. Commit each slice. |
| "The message doesn't matter, the diff is obvious" | Messages are documentation. In six months nobody will read the diff first — they'll `git log --grep`. |
| "I'll squash it all later" | Squashing destroys the development narrative. Prefer clean incremental commits from the start. |
| "I'll split this change later" | Large changes are harder to review, riskier to deploy, harder to revert. Split before submitting. |
| "`--no-verify` just this once" | Every "just this once" is how broken code reaches `main`. Fix the hook instead. |
| "`--amend` is fine, nobody pulled it yet" | That assumption breaks the moment CI pulls or a teammate pulls. Amend only on truly private commits. |
| "I'll add the Co-Author trailer just this once" | The repo policy forbids it. There is no "just this once." |
| "The fix is obvious, I'll skip the test" | Bug fixes ship with a Prove-It test in the same commit. Without the test, the next regression is invisible. |

---

## Verification Checklist

Before every commit:

- [ ] Single logical change
- [ ] Subject: `type(scope): imperative subject`, target ≤72 chars (soft cap 90), lowercase, no trailing period
- [ ] Scope from the table above (or a new one with clear reason)
- [ ] Body explains the *why* (or the change is trivial enough to skip)
- [ ] Breaking changes carry both `!` and `BREAKING CHANGE:` footer
- [ ] `make check` passes clean
- [ ] Bug fix includes a Prove-It test in the same commit
- [ ] **No `Co-Authored-By: Claude` trailer**
- [ ] No obvious secrets in the staged diff
- [ ] No formatter-only churn mixed with behavior changes

Before opening a PR: branch is short-lived, PR title mirrors a commit subject, PR description explains problem and approach, history is a clean sequence of atomic commits.

---

## Examples

**Good (feature, body explains motivation):**

```
feat(tools): enforce imperative + 'Use when' description quality bar

The frontmatter linter accepted descriptions that opened with nouns
or skipped the trigger phrase, which left agents with weak invocation
signals.

Require an imperative first word and a "Use when" clause. Failing
descriptions emit a single error pointing at the file.
```

**Good (trivial, body skipped):**

```
chore(repo): add .gitignore, LICENSE (MIT), and README skeleton
```

**Good (build, body skipped):**

```
build(tools): adopt rich ruff + mypy config matching reference setup
```

**Bad:**

```
fix stuff

updated a bunch of files to fix the thing we discussed

Co-Authored-By: Claude <noreply@anthropic.com>
```

Three failures in one: vague subject, process narration, and a forbidden trailer. Avoid this shape entirely.

# FORGE — Intent-Driven Development

![FORGE logo](images/forge-logo.png)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-261230.svg)](https://github.com/astral-sh/ruff)
[![Type-checked: mypy strict](https://img.shields.io/badge/type--checked-mypy%20strict-2A6DB2.svg)](https://mypy-lang.org/)
[![Tests: pytest](https://img.shields.io/badge/tests-pytest-0A9EDC.svg)](https://docs.pytest.org/)
![Status: alpha](https://img.shields.io/badge/status-alpha-orange.svg)
[![CI](https://github.com/mikeleppane/idd/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/mikeleppane/idd/actions/workflows/ci.yml)
[![Built for: Claude Code](https://img.shields.io/badge/built%20for-Claude%20Code-D97757.svg)](https://code.claude.com)

> **Intent is the source. Spec is the contract. Verification reconciles reality. QA red-teams the result.**
>
> *"The hardest single part of building a software system is deciding precisely what to build. No other part of the work so cripples the resulting system if done wrong. No other part is more difficult to rectify later."*
> — **Fred Brooks**, *No Silver Bullet* (1986)

**⚠️ Alpha.** Expect breaking changes. APIs, schemas, and command surfaces are not yet stable. Not for production-critical workflows. Pin your `.forge/` artifacts via git.

FORGE is a Claude Code plugin that encodes a disciplined Spec-Driven Development lifecycle for working with AI coding agents on real repositories. It sits alongside TDD / BDD / DDD / SDD — a methodology, not a tool.

FORGE optimizes for **disciplined, resumable** software work over speed-first coding. Every artifact it produces earns its place by clarifying intent, preserving context, reducing drift, or verifying reality.

---

## Table of contents

- [Demo](#demo)
- [Quickstart](#quickstart)
- [Install (Claude Code)](#install-claude-code)
- [What it is](#what-it-is)
- [Why use it](#why-use-it)
- [How to use it](#how-to-use-it)
- [Lifecycle](#lifecycle)
- [Tiers](#tiers)
- [TDD enforcement](#tdd-enforcement)
- [The crucible](#the-crucible)
- [Verification](#verification)
- [QA: black-box outsider pass](#qa-black-box-outsider-pass)
- [Research phase](#research-phase)
- [Cross-AI peer review](#cross-ai-peer-review)
- [Per-feature artifacts](#per-feature-artifacts)
- [Use outside Claude Code](#use-outside-claude-code)
- [Compatibility](#compatibility)
- [Configuration](#configuration)
- [Project layout](#project-layout)
- [Comparison vs alternatives](#comparison-vs-alternatives)
- [Security](#security)
- [Contributing](#contributing)
- [License](#license)

---

## Demo

---

## Quickstart

```bash
# 1. Clone + install tooling
git clone https://github.com/mikeleppane/idd.git forge
cd forge
make install               # creates .venv, installs forge-tools[dev]

# 2. Validate the plugin shape
make check                 # ruff + mypy --strict + pytest + validate-health
claude plugin validate .   # Claude Code plugin validator (see Install section)

# 3. Drive your first feature
# In Claude Code, with the plugin loaded:
/forge:do "fix CSV import error handling" --focused
```

Expected on success:

```text
.forge/features/2026-05-10-fix-csv-import-error-handling/
├── SPEC.md          # behavior contract (focused tier)
├── decisions.md     # running ADR log
└── state.json       # tier=focused, current_phase=spec, status=in_progress

Next: /forge:spec --feature 2026-05-10-fix-csv-import-error-handling
```

Then drive the rest of the lifecycle:

```bash
/forge:status                            # see where the active feature is
/forge:next                              # print the next dispatch literal
/forge:spec --feature <id>               # run the current phase
# … each phase command refuses to run unless the previous phase is done.
```

---

## Install (Claude Code)

**Prerequisites:** Python 3.12+, `git`, Claude Code CLI.

```bash
git clone https://github.com/mikeleppane/idd.git forge
cd forge
make install
```

**Reference the plugin from Claude Code.** Until `claude plugins install …` is wired, point your Claude Code config at the cloned path. See the [Claude Code plugin docs](https://code.claude.com/docs/en/plugins-reference) for current syntax. Validate the manifest with:

```bash
claude plugin validate .
```

The `/forge:*` slash commands light up once the plugin is loaded. Verify with `/forge:status` (it will report no active feature).

A formal `claude plugins install …` path is on the roadmap.

---

## What it is

A small set of slash commands, skills, hooks, and JSON-schema-validated artifacts (see [`schemas/`](schemas/)) that walk an AI coding agent through a phased lifecycle — focused tier runs three phases (`spec → execute → verify`), standard runs eight (adds scenarios, plan, crucible, two reviews, ship), full runs eleven (adds refine + research + domain). State is persisted on disk per feature, so any session can be paused, resumed, or handed off without losing context. (A post-ship `qa` phase is on the roadmap; it will activate via `flow_version: 3` once `/forge:do` seeds it.)

## Why use it

You already have an AI coding agent. FORGE adds the missing scaffolding around it:

- **Intent** is treated as the project north-star — the *why* behind any change.
- **Spec** is the contract for intended behavior — adversarially refined before code is written.
- **Verification** reconciles spec, code, tests, runtime behavior, and user confirmation — three layers, not just "tests pass."
- **Crucible** is an adversarial post-plan ritual (assumptions inversion → adversarial Q&A → pre-mortem) that produces a shared `UNDERSTANDING.md` between you and the agent.
- **QA** is a fresh-outsider black-box pass after ship — an agent with no implementation context exercises the artifact from a user's perspective, attempts edge cases, and red-teams it. Verdict gates the pre-PR prompt; advisory post-merge.
- **TDD** is mechanical, not aspirational — every acceptance criterion requires a paired test commit before its impl commit, enforced by `tools.validate.tdd_evidence`.
- **Context discipline** keeps the main thread under a hard token budget by isolating slices in subagents and preventing context bleed.
- **Cross-AI review** uses a different model family (Claude ↔ GPT) as a second-opinion reviewer, reinforcing the adversarial shared-understanding goal. Manual mode (default) writes a self-contained prompt to disk for paste-into-other-CLI workflows; auto mode (opt-in) dispatches an external CLI directly. Both modes apply a configurable redaction filter before any prompt materialises. See [Cross-AI peer review](#cross-ai-peer-review) for the full surface.

FORGE pays off when the cost of building the wrong thing, losing context, drifting from intent, or losing your own mental model of the code is higher than the cost of a disciplined workflow. It is **not** the fastest path to code. It *is* a clear path from intent to verified behavior — without surrendering your understanding of the system along the way.

---

## How to use it

Install the plugin (see below), then drive a feature through the lifecycle with one command:

```text
/forge:do "<your idea>" [--focused | --standard | --full]
```

`/forge:do` runs preflights, scans for capability conflicts, picks a tier, and seeds the per-feature folder under `.forge/features/<id>/`. It then dispatches the next slash command in the chosen tier.

Prefer to drive each phase manually? Each phase has its own slash command (`/forge:refine`, `/forge:spec`, `/forge:domain`, `/forge:scenarios`, `/forge:plan`, `/forge:crucible`, `/forge:review`, `/forge:execute`, `/forge:verify`, `/forge:ship`, `/forge:qa`) — research is currently skipped (recorded in `state.json.skipped`); manual research before spec is acceptable. Each command refuses to run unless the previous phase is complete — the on-disk state machine is the source of truth.

---

## Lifecycle

```text
refine → research → spec → domain → scenarios → plan → crucible → review → execute → verify → ship → qa
```

| Phase | Output | Purpose |
| --- | --- | --- |
| **refine** | refined idea statement | Sharpen a vague idea into a single-feature scope |
| **research** | `RESEARCH.md` | Codebase + external library discovery before the spec is locked. Auto-runs on `--full`; opt-in via `--research` on `--standard`; refused on `--focused`. Emits codebase findings, external docs (with grounding-mode citations), domain notes, and risks. |
| **spec** | `SPEC.md` | Behavior contract: Intent, Domain, Scope, Scenarios, Acceptance, Open Questions |
| **domain** | `DOMAIN.md` (full tier) — glossary, bounded contexts, aggregates, invariants | Ubiquitous language with auto-rendered bounded-context Mermaid; SPEC.md `# Domain` becomes a pointer |
| **scenarios** | Gherkin scenarios in `SPEC.md` | BDD acceptance criteria; auto-escalates to `.feature` files when the project supports it |
| **plan** | `PLAN.md` | Vertical slices and waves of parallelizable tasks; file-bound |
| **crucible** | `UNDERSTANDING.md` | Adversarial ritual: assumptions inversion → adversarial Q&A → pre-mortem |
| **review** | `REVIEW.plan.md` / `REVIEW.code.md` | Layered self + heavy + cross-AI reviews with convergence loops |
| **execute** | code + tests | Slice-isolated, subagent-bounded, wave-parallel implementation. TDD pairing enforced — every AC requires a `test(...)` commit before its `feat(...)` commit. |
| **verify** | `VERIFICATION.md` | Three layers: code audit + scenario execution + conversational UAT |
| **ship** | merged change + updated canonical spec or delta proposal | Reconcile feature spec with shipped capability spec. Pre-PR QA gate prompt available. |
| **qa** | `QA.md` with verdict + confidence | Fresh-outsider black-box pass: acceptance + edge probing + adversarial + NR regrep. Terminal phase; archives on completion. |

Phases can be skipped via flags or selected automatically by `/forge:do`, which estimates the right tier and routes accordingly.

---

## Tiers

`--focused` means **narrow**, not necessarily fast. Pick the tier that matches the change's risk and surface area, not your patience.

| Tier | Phases | Use when |
| --- | --- | --- |
| `--focused` | `spec → execute → verify` | One-file fixes, surgical changes, well-understood bugs |
| `--standard` | `spec → scenarios → plan → crucible → review → execute → review → verify → ship → qa` | Most features; cross-file changes; non-trivial behavior |
| `--full` | entire pipeline through `qa` | New subsystems, cross-cutting refactors, anything requiring deep research and DDD |

The standard tier runs review twice (against `PLAN.md`, then against the code diff). Focused finishes at `verify` (no ship, no qa — `/forge:ship` aborts on focused); standard and full both end in `qa`. The `forge-context-budget` and `forge-subagent-dispatch` skills enforce per-subagent token budgets at every dispatch; `tools.validate.tdd_evidence` enforces paired test/impl commits in execute; `tools.validate.qa_shape` enforces the QA artifact contract on ship/qa exit.

---

## TDD enforcement

> *"TDD doesn't drive good design. TDD gives you the opportunity to think about good design every few minutes."*
> — **Kent Beck**, *Test-Driven Development: By Example*

In FORGE, TDD is **mechanical, not aspirational**. Every acceptance criterion in `SPEC.md` produces a paired commit sequence in execute:

```text
test(<scope>): AC-3 failing — empty CSV returns InvalidInput
feat(<scope>): AC-3 — handle empty CSV in importer
```

`tools.validate.tdd_evidence` walks the execute-phase commit range and refuses to advance unless every AC has a `test(...)` commit chronologically *before* its matching `feat(...)` commit. ACs without paired tests block phase transition.

Exceptions exist (e.g., pure-config changes, generated artifacts) but require an explicit **TDD Exception ADR** in `decisions.md` with rationale. Override is auditable, not silent.

---

## The crucible

> *"The first principle is that you must not fool yourself — and you are the easiest person to fool."*
> — **Richard Feynman**, Caltech commencement (1974)

The crucible is FORGE's most opinionated piece — an adversarial ritual run *after* planning and *before* execution:

1. **Assumptions inversion.** Every load-bearing assumption is inverted: "what if this is wrong?"
2. **Adversarial Q&A.** The agent argues against the plan, surfacing the strongest objections.
3. **Pre-mortem.** Imagine the change has shipped and failed — what failed and why?

The output is `UNDERSTANDING.md` — a record of shared understanding between you and the agent. Code that doesn't survive the crucible doesn't get written.

---

## Verification

> *"Have the conversation. Then automate the conversation."*
> — **Dan North**, paraphrased from the BDD origin essays

Three layers, all rolled into `VERIFICATION.md`:

1. **Code audit.** Static review of the implementation against the spec.
2. **Scenario execution.** Acceptance scenarios run against the actual code (BDD when supported, manual checklist otherwise).
3. **Conversational UAT.** Structured back-and-forth with the user to confirm behavior matches intent.

A feature ships only after all three layers pass.

---

## QA: black-box outsider pass

After verify and before archive, FORGE runs a fresh outsider QA pass. The QA agent has only `SPEC.md` and an opaque `ArtifactDescriptor` (`{kind: cli|library|service|ui|other, identifier: <opaque string>}`) — no implementation context, no test files, no plan. Four sections, each producing a status and findings phrased in user-facing terms:

1. **Acceptance.** Does the shipped artifact deliver what `SPEC.md` promised, from a user's perspective? Verdict: `delivers | partial | does-not-deliver`.
2. **Edge probing.** What would a normal user mistype, misuse, or stumble into?
3. **Adversarial.** Capped red-team subagent (5 min walltime, 50 attempts) trying to break the feature.
4. **NR regrep.** Re-greps the merged tree against every Negative Requirement in `SPEC.md` to catch re-introductions.

The QA artifact is `QA.md` with frontmatter `verdict` + `confidence` (high|partial|low), aggregated mechanically from the four section statuses. `tools.validate.qa_shape` enforces the contract.

### Two timing modes

- **Pre-PR gate (opt-in).** `/forge:ship` prompts: `"Run QA before creating PR? [Y/n]"` (default Y for `--standard`/`--full`, N for `--focused`). On accept, QA runs against the working tree at HEAD of the feature branch. Verdicts: `delivers` continues ship; `partial` re-prompts the user; `does-not-deliver` blocks PR creation. `--qa-override-with-rationale "<reason>"` records an ADR'd override in `decisions.md`.
- **Post-merge phase (terminal).** `/forge:qa --against merged` runs against the merged artifact after ship. Required for `--full`; opt-in for `--standard`/`--focused`. Phase flips `state.json.phases.qa.status` to `done` and triggers archive.

The skill is the same in both timings; only the `--against` flag differs.

---

## Research phase

Before locking a spec for non-trivial work, the research phase emits a `RESEARCH.md` with four sections: codebase findings (top-level layout, modules touched, extension points), external docs (library-by-library, with citation), domain notes (glossary candidates), and risks surfaced. The phase auto-runs on `--full`; opt into it on `--standard` with `/forge:do --standard --research "<idea>"`; `--focused` refuses.

External library docs are gathered through one of five grounding modes:

| Mode | When | Citation format |
| --- | --- | --- |
| `full` | Context7 MCP server installed and reachable | `[context7:<library_id>:<snippet_id>]` |
| `degraded` | No Context7, no BYOD coverage, no WebSearch fallback | none — explicit unavailable marker required in body |
| `websearch` | Opt-in via `.forge/config.json` `research.websearch_fallback: true` (privacy implication; sends queries externally) | `[websearch:<url>]` |
| `byod` | All extracted libraries have files at `.forge/external-docs/<library>.md` | `[byod:<library>:<section-anchor>]` |
| `byod-partial` | Mixed: some libraries covered locally, some missing | mixed; uncovered libraries fall through to the degraded rule |

The bring-your-own-docs (BYOD) pattern lets air-gapped repos pre-stage authoritative docs locally — drop a markdown file at `.forge/external-docs/<library>.md` and the research subagent reads it as the citation source. Files older than `research.byod_stale_days` (default 90) emit a staleness warning.

Ecosystem detection is **pluggable**: out of the box, FORGE recognizes Python, Node, Rust, Go, Ruby, Java, .NET, Elixir, PHP, Swift, and Dart manifests. Polyglot repos (e.g., Node frontend + Python backend) return multiple ecosystem records; library extraction unions across them. Repos using an ecosystem FORGE doesn't recognize fall back to a generic dir-walk and a one-time prompt to pin the ecosystem via `.forge/config.json`. Adding support for a new ecosystem is a single-file plugin — no skill prose changes.

The grounding mode and BYOD coverage are recorded in the RESEARCH.md frontmatter and surfaced in the ship-time risk summary.

---

## Cross-AI peer review

`/forge:review --cross-ai` delegates the review pass to an external CLI (codex, claude, or gemini) for an independent second opinion. Two modes:

- **Manual (default).** The skill builds a self-contained prompt (spec excerpt, diff, finding format), applies the redaction filter, prints a disclosure summary (file count, diff LOC, estimated tokens, estimated USD with ±50% precision, redaction summary), then writes the prompt to `.forge/features/<id>/cross-ai/<target>-<ts>-prompt.md`. You dispatch the external CLI yourself; paste the response back via `/forge:review --cross-ai-paste <path>`. Manual mode performs no external dispatch and does not consume the auto-mode dispatch-approval cache.
- **Auto (opt-in).** With `cross_ai.mode: auto` in `.forge/config.json` or the `--auto` flag per invocation, the skill spawns the external CLI directly via `subprocess.run` and captures the response. First-run-per-repo requires you to type `APPROVE` (cached as `cross_ai.dispatch_approved_at`); a cost-warn gate fires every invocation when the estimated USD exceeds `cross_ai.cost_warn_threshold_usd` (default `$0.50`) and requires `APPROVE-COST` (or `--skip-cost-warn` with a `decisions.md` deviation row).

Redaction runs deterministically before any prompt materialization. The default deny-list strips `.env`, credentials, secrets, `.aws/`, and `.ssh/` files entirely; user-extendable via `cross_ai.redaction.deny_globs`, `deny_regex`, and `fatal_regex` in `.forge/config.json`. `fatal_regex` matches refuse dispatch unconditionally — even in auto mode.

Findings parsed from the external response are merged into `REVIEW.<target>.md` with `Source: external-<model>` and feed the existing convergence loop on the same 3-cycle cap.

---

## Per-feature artifacts

> *"The heart of software is its ability to solve domain-related problems for its user. All other features, vital though they may be, support this central purpose."*
> — **Eric Evans**, *Domain-Driven Design* (2003)

Every FORGE feature lives in `.forge/features/<id>/` with a small set of contracts:

- `SPEC.md` — the behavior contract
- `RESEARCH.md` — optional, for research tier and above
- `DOMAIN.md` — full-tier source of truth for glossary, bounded contexts, aggregates, invariants. SPEC.md `# Domain` becomes a pointer to it.
- `UNDERSTANDING.md` — output of the crucible
- `PLAN.md` — file-bound vertical slices and waves
- `REVIEW.plan.md` / `REVIEW.code.md` — per-target review findings and convergence cycles
- `VERIFICATION.md` — three-layer verification record
- `QA.md` — fresh-outsider black-box acceptance record (verdict + confidence + four sections)
- `decisions.md` — running log of decisions and rationale (includes TDD Exception ADRs and QA Override ADRs)
- `state.json` — phase / slice / wave state for resumption (carries `flow_version: 3` for v3 features)
- `.forge/logs/<feature_id>.jsonl` — optional local-only event log written via `tools.feature_log` when callers append events; gitignored, never sent over the network

Canonical capability specs live in `.forge/specs/<capability>/SPEC.md`. Feature specs are working artifacts and are merged or archived against canonical specs at ship time. Changes to shipped capabilities flow through OpenSpec-style delta proposals under `.forge/changes/<id>/proposal.md` via `/forge:change`.

A project-wide `.forge/CONSTITUTION.md` carries CRITICAL / SHOULD / MAY articles. Today these are **advisory** — surfaced at every phase entry and required as a ship-time acknowledgement (the user explicitly accepts that the diff respects them). Detection-driven BLOCK gates that mechanically refuse non-compliant changes are roadmapped, not implemented.

### What the artifacts look like

`SPEC.md` (excerpt):

```markdown
---
feature_id: 2026-05-10-fix-csv-import-error-handling
tier: focused
flow_version: 3
---

# Intent
Users importing malformed CSVs currently see a stack trace. We want a
clear, actionable error message that names the offending row.

# Scope
- IN:  CSV importer error path
- OUT: file-format detection, encoding negotiation

# Acceptance
- AC-1: empty CSV returns `InvalidInput("file is empty")`.
- AC-2: malformed row N returns `InvalidInput("row N: <reason>")`.
- AC-3: well-formed CSV continues to import unchanged.

# Negative requirements
- NR-1: importer must not raise on user input — only return Result.
```

`state.json` (excerpt, focused tier mid-execute):

```json
{
  "feature_id": "2026-05-10-fix-csv-import-error-handling",
  "tier": "focused",
  "flow_version": 3,
  "current_phase": "execute",
  "phases": {
    "spec":    { "status": "done",        "completed_at": "2026-05-10T10:14:02Z" },
    "execute": { "status": "in_progress", "started_at":   "2026-05-10T10:18:55Z" },
    "verify":  { "status": "pending" }
  },
  "skipped":    [{ "phase": "research", "reason": "research deferred; manual research acceptable" }],
  "deviations": [],
  "commits":    ["abc1234", "def5678"]
}
```

---

## Use outside Claude Code

[`AGENTS.md`](AGENTS.md) at the repo root is a portable discovery manifest with the canonical command + skill list. Cursor, Aider, and Codex consume the same plain-markdown skills and commands. Full portability validation is in progress; the markdown source is portable today.

---

## Compatibility

| Surface | Status | Notes |
| --- | --- | --- |
| Claude Code (latest) | ✅ supported | primary target; slash commands + skills + hooks all wired |
| Cursor | 🟡 source-portable | reads `AGENTS.md` + plain-markdown commands; budget hooks not enforced |
| Aider | 🟡 source-portable | same; manual phase discipline |
| Codex CLI | 🟡 source-portable | same |
| GitHub Copilot Chat | 🔴 untested | discovery manifest format compatible in principle |
| Python | ✅ 3.12+ required | `tools/` uses 3.12 syntax (`type` aliases, PEP 695) |
| OS | ✅ Linux / macOS · 🟡 Windows | tested on Linux + macOS; Windows via WSL works, native untested |

---

## Configuration

Per-feature state lives in `.forge/features/<id>/state.json` (created by `/forge:do` or `/forge:spec`). Project-wide configuration (default tier, cross-AI provider, context-budget overrides, auto-escalation rules) is on the roadmap.

The tooling itself (state machine, frontmatter linter, schema validator, archive helpers) is a small Python package shipped in `tools/`.

---

## Project layout

```text
.
├── .claude-plugin/plugin.json   Claude Code manifest
├── AGENTS.md                    portable discovery manifest
├── README.md                    you are here
├── commands/                    /forge:* slash commands
├── skills/                      ambient + invokable skills
├── hooks/                       PreToolUse hooks (budget enforcement)
├── templates/feature/           per-feature artifact templates
├── schemas/                     JSON Schemas for state and frontmatter
├── tools/                       Python: state machine, linters, schema validator
└── tests/                       unit + smoke + reference fixtures
```

---

## Comparison vs alternatives

| Tool | Niche | How FORGE differs |
| --- | --- | --- |
| **Aider** | terminal AI pair-programmer | Aider is a smart edit loop; FORGE is a phased lifecycle around any agent. Use Aider *inside* an execute slice if you like; FORGE governs the slice. |
| **OpenSpec** | spec-as-code with delta proposals | FORGE adopts OpenSpec-style deltas (`/forge:change`) but adds a full pre-spec lifecycle (refine, domain, scenarios, crucible) and post-spec verification + QA. |
| **GitHub Spec Kit** | spec → plan → tasks scaffolding | Spec Kit covers spec/plan/tasks; FORGE adds adversarial crucible, mechanical TDD pairing, three-layer verify, fresh-outsider QA, and on-disk state machine. |
| **BMAD-Method** | role-played agent orchestration | BMAD scripts agent personas; FORGE encodes a state machine over artifacts. Personas optional. |
| **Plain TDD/BDD/DDD** | discipline as principle | FORGE is the union of these as a single mechanical workflow, with the validators wired in. |

If you want **fast unstructured AI editing**, use Aider/Cursor directly. If you want **disciplined, resumable, auditable** AI work where the cost of building the wrong thing is high — FORGE.

---

## Security

FORGE persists artifacts on disk and via git. Treat them like source code:

- **`state.json.routing.idea` stores your prompt verbatim.** Do not paste secrets, API keys, customer PII, or internal hostnames into `/forge:do`. The text is committed alongside other `.forge/` artifacts.
- **`SPEC.md`, `PLAN.md`, `decisions.md`, `QA.md` are committed.** Anything you tell the agent about the system ends up in git history. Use `.gitignore` patterns under `.forge/features/` for sensitive features.
- **`.forge/logs/<feature_id>.jsonl`** is gitignored and never sent over the network — local-only event log.
- **Cross-AI review (when wired)** sends review artifacts to a third-party model provider. Review the `cross-ai-config` schema before enabling; redaction filter (`tools.redaction`) strips known secret patterns but is best-effort.
- **Constitution acknowledgement is advisory** today — it does not mechanically prevent unsafe code. Treat it as a checklist, not a guard.

Report security issues privately via GitHub Security Advisories on the repo.

---

## Contributing

FORGE is in early active development. Issues and feedback are welcome.

**Dev loop:**

```bash
make install      # creates .venv, installs forge-tools[dev]
make check        # ruff + mypy --strict + pytest + validate-health (run before every commit)
make format       # apply ruff formatter
make test         # pytest only
make typecheck    # mypy strict only
python -m tools.validate --target health   # planning-directory health
```

**Conventions:**

- Python 3.12+, ruff (lint + format), mypy `--strict`, pytest.
- [Conventional Commits](https://www.conventionalcommits.org/) with required scopes (e.g., `feat(routing):`, `fix(archive):`, `test(state):`). Atomic commits — one logical change per commit.
- No `Co-Authored-By: Claude` trailers in commit messages.
- No internal planning labels in code, docstrings, comments, or commit messages — those belong in the PR description.
- All planning artifacts (`SPEC.md`, `PLAN.md`, etc.) must pass `python -m tools.validate`.
- For larger features, follow FORGE's own lifecycle (`/forge:do`).

See [`AGENTS.md`](AGENTS.md) for the canonical command/skill manifest and contributor guidance.

---

## License

MIT — see [LICENSE](LICENSE).

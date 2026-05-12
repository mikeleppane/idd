---
name: forge-resync-agents
description: Re-sync AGENTS.md / CLAUDE.md / README.md prose into enforcement mechanisms — Constitution articles, .forge/conventions.json entries, dispatch-hook TODOs, or advisory log. Use when /forge:amend-constitution --resync-agents fires.
model: sonnet
disable-model-invocation: true
---

# FORGE Resync Agents

This skill walks the user through extracting prose conventions from
`AGENTS.md` / `CLAUDE.md` / `README.md` and routing each rule to the right
enforcement mechanism. The taxonomy (hook / validator / reviewer-tag /
advisory) is the same one used to design the conventions runtime — each
mechanism has its own enforcement surface and its own write target, so a
rule that wants to be checked at dispatch time should not be paraphrased
into a Constitution article and a rule that needs reviewer judgment
should not be jammed into a regex.

## When this skill applies

Triggered when `/forge:amend-constitution --resync-agents` fires. The
skill assumes the project has at least one of `AGENTS.md` / `CLAUDE.md` /
`README.md` at the repo root. If all three are absent, the skill presents
**one** `AskUserQuestion` offering only `[C]ancel` / `[E]xit anyway`;
either choice aborts with no disk mutation.

## Inputs

- `repo_root` — absolute path to the repository root.
- `decisions_path` — default `<repo_root>/decisions.md`; project may
  override.

## Enforcement-mechanism taxonomy (reference)

| Mechanism | What it sees | Example fit |
|---|---|---|
| **hook-enforced** | dispatch payload / tool input pre-call | dispatch-brief citation rule, files_in_scope shape |
| **validator-enforced** | repo artifacts (commits, plans, state, review files) post-action | git-conventions, frontmatter, spec semantic |
| **reviewer-tagged** | diff + Constitution articles, free-text findings | API-shape rules, naming conventions |
| **advisory** | dispatch context only | tone, style preferences |

## Steps

1. **Collect signals.** Call
   `tools.constitution_amend.collect_resync_signals(repo_root)`. The
   helper returns a `BootstrapSignals` dataclass with fields
   `files: list[SignalFile]`, `dropped: list[PurePosixPath]` (every path
   skipped — deny-glob match OR secret-content match — surfaced together
   because the two causes are operationally equivalent for the resync
   flow), `truncated: list[PurePosixPath]`, and `total_bytes: int`. The
   `dropped_for_secrets` attribute is a back-compat alias for `dropped`
   and may be used by older orchestrators. Surface to the user the
   collected file count, every dropped path, and every truncated path
   (capped at 16 KiB). Python performs zero LLM calls and zero network
   access at this step.

2. **Refuse if no source files.** If `signals.files` is empty, present
   **one** `AskUserQuestion`:

   > No AGENTS.md / CLAUDE.md / README.md found. Nothing to resync. Cancel?

   Options (single-select):

   - `[C]ancel` — abort cleanly.
   - `[E]xit anyway` — abort cleanly.

   Either choice aborts with no disk mutation.

3. **Draft the convention inventory in-session.** Read the signal payload.
   For each `MUST` / `SHOULD` / `SHALL` / forbidden / required pattern in
   the prose, emit an inventory row of the form:

   ```
   - **<rule text>** (from <source_file>:<line>)
     - Mechanism: <hook | validator | reviewer-tag | advisory>
     - Justification: <one-sentence reason>
     - Proposed shape: <what gets added if accepted>
   ```

   Use the taxonomy table to pick the mechanism:

   - Dispatch-text rules ("dispatches MUST cite X", "subagent prompts
     MUST include Y") → `hook`.
   - Commit / PR / diff-shape rules ("commits MUST follow Conventional
     Commits", "PRs MUST link an issue") → `validator`.
   - Code-shape rules ("functions SHOULD be ≤80 lines", "no inline
     secrets") → `reviewer-tag`.
   - Style / tone rules without a clear enforcement surface →
     `advisory`.

4. **Show full inventory to user.** Print the inventory verbatim. Then
   present **one** `AskUserQuestion` (single-select, exactly five
   options):

   > Convention inventory ready. What now?

   - `[r]eview-one-by-one` — walk each entry individually (see step 5).
   - `[a]ccept-all` — apply every entry to its proposed mechanism
     (see step 7).
   - `[e]dit-inventory` — open the inventory in `$EDITOR`
     (see step 8).
   - `[s]kip` — discard, no disk mutation.
   - `[c]ancel` — abort.

   This is a single-question turn. Do not batch any clarifier with it.

5. **Per-entry review (when `[r]eview-one-by-one`).** For each inventory
   row, present **one** `AskUserQuestion`:

   > Entry N of M: <rule text>. Proposed mechanism: <X>.

   Options (single-select):

   - `[a]ccept`
   - `[c]hange-mechanism`
   - `[d]rop-entry`
   - `[s]top-here` — apply accepted-so-far, drop the rest.

   On `[c]hange-mechanism`, follow up with **one**
   `AskUserQuestion` to pick `hook | validator | reviewer-tag | advisory`.
   Each clarifier is its own turn — never batch the mechanism picker with
   the per-entry accept selector.

6. **Apply by mechanism after the review loop.** Group accepted entries
   by the mechanism they ended up on and apply:

   - **`hook` entries** → surface as a TODO list:

     > The following dispatch-brief rules need hook-authorship. Add them
     > to `.forge/conventions.json` with `scope: ["dispatch_brief"]` and
     > a `pattern_kind` of `required_text` or `forbidden_text`:
     >  - <rule text> (from <source>:<line>)
     >  - ...

     If the user accepts, call
     `tools.constitution_amend.append_conventions_entries` with the
     derived `Convention` records (default `pattern_kind=required_text`,
     `scope=("dispatch_brief",)`, `severity=HIGH`). The hook
     (`hooks/check_budget.py`) already consumes any
     `scope: dispatch_brief` rule at PreToolUse time — no further hook
     authorship is needed for the rule to take effect.

   - **`validator` entries** → derive a `Convention` record with the
     appropriate scope (`commit_body` for commit rules, `diff` for
     diff/filename rules) and `pattern_kind`. Append via
     `append_conventions_entries`. The conventions validator
     (`tools.validate.conventions`) consumes these at review time.

   - **`reviewer-tag` entries** → loop back to the Constitution amend
     flow. Surface a TODO:

     > These rules want Constitution articles. Run
     > `/forge:amend-constitution` and add an article each, with the
     > rule body suggested above.

     Do NOT auto-create articles — the article-drafting interactive
     loop already exists in `forge-amend-constitution`; the user runs
     the manual flow.

   - **`advisory` entries** → call
     `tools.constitution_amend.log_advisory_entries(repo_root=...,
     entries=[AdvisoryEntry(rule_text=..., source_file=..., source_line=...), ...])`.
     The helper auto-creates `decisions.md` when absent and appends a
     single ADR row in the canonical shape (one bullet per advisory
     entry). Do NOT hand-write the ADR — keep the path symmetric with
     the hook / validator dispositions so future schema changes are
     applied in one place.

7. **`[a]ccept-all` branch.** Apply every inventory entry to its
   proposed mechanism without per-entry confirmation. The four mechanism
   dispositions in step 6 still apply — the difference is the absence of
   intermediate prompts.

8. **`[e]dit-inventory` branch.** Write the inventory to a tempfile,
   open `$EDITOR` against it, then read back the edited file. Re-parse
   the rows. If the user reshuffled mechanisms or dropped rows, honor
   the edited file as authoritative and proceed to step 6's mechanism
   dispatch with the new groupings.

## Mechanism precedence

When a single logical convention overlaps multiple mechanisms (e.g.,
a banned commit trailer matches both a `forbidden_text` rule in
`.forge/conventions.json` AND a `trailer_ban_patterns` entry in
`.forge/config.json:git_conventions`), the precedence is:

1. **git-conventions** wins for commit-message-shape rules. Always
   ship trailer bans in `git_conventions.trailers.ban_patterns` —
   not in conventions.json. The git-conventions validator has the
   most specific match (full trailer parsing, RFC 5322 alignment)
   and the strictest gate (BLOCK with no ACK path).
2. **conventions.json** owns dispatch-brief and diff-content rules.
3. **Constitution articles** own architectural and policy rules
   that span diffs, commits, AND review prose.

The resync skill SHOULD refuse to append a `forbidden_text` /
`required_text` rule that overlaps an existing
`git_conventions.trailers.ban_patterns` entry. Surface the conflict
to the user with a hint to remove one or the other. (This is
documented intent; a Python guard for the refusal is not yet
implemented — when authoring conventions, prefer the surface called
out above and verify no overlap by inspection.)

## Sequential-question contract (locked)

This skill follows the **one-question-per-turn** pattern that already
governs `skills/forge-bootstrap-constitution/SKILL.md` step 5,
`skills/forge-refine/SKILL.md` step 6a, and
`skills/forge-crucible/SKILL.md` Adversarial Q&A.

Each `AskUserQuestion` in this skill is its own turn:

1. The **no-source-files refusal** (step 2) — two options.
2. The **review-all / accept-all / edit / skip / cancel selector**
   (step 4) — five options.
3. Any **per-entry review** prompt (step 5) — four options per entry.
4. The **change-mechanism clarifier** (step 5) — four options, asked
   only on `[c]hange-mechanism`.

Batched multi-question prompts are forbidden in this skill.

## Failure modes

- **No source files** → present the cancel-only refusal in step 2 and
  abort cleanly.
- **`append_conventions_entries` raises `AmendError`** → surface the
  helper's message verbatim; offer to drop the offending entry from the
  pending dispatch group or cancel the resync.
- **User cancels mid-loop** → no disk mutation; any partial accept-list
  is discarded.
- **`$EDITOR` returns a malformed inventory (edit-branch)** → surface
  the parse error and offer one repair round in the editor; on a second
  failure, fall through to `[c]ancel`.

## State writes

- `.forge/conventions.json` — appended via
  `tools.constitution_amend.append_conventions_entries` when validator
  or hook entries get accepted.
- `decisions.md` — one ADR row per `append_conventions_entries` call,
  plus a separate row for any advisory items the user logged in step 6.
- No Constitution article writes — `reviewer-tag` entries route the
  user to `/forge:amend-constitution` manually.

## See also

- `tools.constitution_amend.collect_resync_signals` — bounded I/O
  helper that produces the doc-only signal payload (same shape and
  bounds as `collect_bootstrap_signals`, restricted to `AGENTS.md` /
  `CLAUDE.md` / `README.md`).
- `tools.constitution_amend.append_conventions_entries` — atomic
  JSON-append helper that writes `.forge/conventions.json` and the
  decisions ADR row.
- `tools.validate.conventions` — runtime validator that consumes the
  appended entries against commit bodies and diffs.
- `tools.validate.git_conventions` — separate validator for
  commit-message shape rules (Conventional Commits).
- `hooks/check_budget.py` — PreToolUse hook that consumes
  `scope: dispatch_brief` rules without further authorship.
- `skills/forge-amend-constitution` — Constitution article path for
  reviewer-tag entries.

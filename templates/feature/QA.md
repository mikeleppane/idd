---
feature_id: <YYYY-MM-DD-slug>
shipped_at: <ISO-8601>
qa_at: <ISO-8601>
verdict: <delivers|partial|does-not-deliver>
confidence: <high|partial|low>
flow_version: 3
---

# QA Acceptance Record

> Outside black-box review. The QA agent runs with NO implementation context — only SPEC.md + shipped artifact path —
> and reports from a fresh user perspective. Every finding is phrased in user-facing terms (observable behavior),
> not internal terms (file paths, function names). The aggregate verdict + confidence is computed mechanically from
> per-section status; do not edit by hand.

# Acceptance

> Does the shipped artifact deliver what SPEC.md promised, from a user's perspective?

- **Status:** <delivers|partial|does-not-deliver>
- **Spec promises checked:** <count>
- **Promises met:** <count>
- **Findings:**
  - <one bullet per gap: user-facing observation, not internal cause>
- **Evidence:** <transcript, screenshot path, or shipped-artifact identifier>

# Edge Probing

> What a normal user might mistype, misuse, or stumble into.

- **Status:** <pass|fail|partial>
- **Edges probed:** <count>
- **Failures observed:** <count>
- **Findings:**
  - <one bullet per surfaced edge: scenario + observed behavior>
- **Evidence:** <transcript path or reproducer commands>

# Adversarial

> Red-team probe: capped subagent attempts to break the feature.

- **Status:** <pass|fail|partial>
- **Walltime budget:** <minutes / 5 max>
- **Attempts:** <count / 50 max>
- **Breakages found:** <count>
- **Findings:**
  - <one bullet per breakage: severity + scenario + reproducer>
- **Evidence:** <subagent return path>

# NR Regrep

> Pattern safety net: re-greps the merged tree against every Negative Requirement.

- **Status:** <pass|fail>
- **Negative Requirements scanned:** <count>
- **Violations re-introduced:** <count>
- **Findings:**
  - <one bullet per re-introduction: NR id + offending path:line>
- **Evidence:** <command output path or commit sha scanned>

---

## Verdict + confidence aggregation rule

Verdict is mechanical from `# Acceptance` Status:

- `delivers` ↔ Acceptance Status is `delivers`.
- `partial` ↔ Acceptance Status is `partial`.
- `does-not-deliver` ↔ Acceptance Status is `does-not-deliver`.

Confidence aggregates per-section Status across all four sections:

- `high` ↔ every section is `pass` (or `delivers` for Acceptance) AND no high-severity adversarial finding.
- `partial` ↔ at most one section is `partial`, zero are `fail`/`does-not-deliver`.
- `low` ↔ any section is `fail`/`does-not-deliver`, OR more than one is `partial`.

`/forge:qa` writes both fields; do not edit by hand.

---
feature_id: 2026-05-08-qa-blocks-feature
shipped_at: 2026-05-08T10:00:00Z
qa_at: 2026-05-08T11:00:00Z
verdict: delivers
confidence: high
flow_version: 3
---

# QA Acceptance Record

> Hand-authored fixture intentionally inconsistent: the frontmatter
> declares delivers + high while the section statuses below report a
> non-delivering acceptance and a partial edge sweep. Validator must
> emit two BLOCK findings.

# Acceptance

- **Status:** does-not-deliver
- **Spec promises checked:** 4
- **Promises met:** 1
- **Findings:**
  - greeting did not match the documented text in the happy invocation
- **Evidence:** transcript-acceptance-block-001

# Edge Probing

- **Status:** partial
- **Edges probed:** 5
- **Failures observed:** 1
- **Findings:**
  - mistyped subcommand surfaced an unhelpful error message
- **Evidence:** transcript-edge-block-001

# Adversarial

- **Status:** pass
- **Walltime budget:** 5
- **Attempts:** 8
- **Breakages found:** 0
- **Findings:**
  - none
- **Evidence:** transcript-adversarial-block-001

# NR Regrep

- **Status:** pass
- **Negative Requirements scanned:** 1
- **Violations re-introduced:** 0
- **Findings:**
  - none
- **Evidence:** abc1234

---
id: 2026-05-06-anchors-missing-path
status: draft
tier: standard
created: 2026-05-06
capability: anchors-missing-path
---

# Intent

A SPEC whose Codebase Anchors row points at a path that does not exist.

# Codebase Anchors

- `pkg/missing.py:hello` — references a path that does not exist

# Acceptance Criteria

1. Missing anchor path raises a HIGH finding.

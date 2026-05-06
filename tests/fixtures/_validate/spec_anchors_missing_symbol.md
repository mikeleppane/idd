---
id: 2026-05-06-anchors-missing-symbol
status: draft
tier: standard
created: 2026-05-06
capability: anchors-missing-symbol
---

# Intent

A SPEC whose Codebase Anchors row resolves the path but the symbol is absent.

# Codebase Anchors

- `pkg/mod.py:absent_symbol` — drift / rename detector

# Acceptance Criteria

1. Missing anchor symbol raises a MEDIUM finding.

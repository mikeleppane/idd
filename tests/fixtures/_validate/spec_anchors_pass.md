---
id: 2026-05-06-anchors-pass
status: draft
tier: standard
created: 2026-05-06
capability: anchors-pass
---

# Intent

A SPEC whose Codebase Anchors all resolve to real files and symbols.

# Codebase Anchors

- `pkg/mod.py:hello` — main entrypoint
- `pkg/__init__.py` — package marker
- `lib/util.ts:shout` — frontend helper

# Acceptance Criteria

1. Anchors resolve cleanly under repo_root.

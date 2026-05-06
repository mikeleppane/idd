---
id: 2026-05-06-anchors-traversal
status: draft
tier: standard
created: 2026-05-06
capability: anchors-traversal
---

# Intent

A SPEC whose Codebase Anchors row traverses out of repo_root. This must be blocked.

# Codebase Anchors

- `../../../etc/passwd:root` — path-traversal escape attempt

# Acceptance Criteria

1. Traversal-escaping anchor path is blocked.

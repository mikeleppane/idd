---
name: forge-validate
description: Run the structural validator over FORGE artifacts and surface findings. Use when the user wants to check Constitution shape, delta proposals, NR placement, capability uniqueness, or the overall .forge/ health.
disable-model-invocation: true
---

# FORGE Validate

## Goal

Run `python -m tools.validate` against a target and surface findings to the user.

## Inputs

- `--target` (required), one of:
  - **Per-file (positional `path` = artifact)**:
    - `spec` — NR placement + frontmatter schema (SPEC.md).
    - `plan` — frontmatter schema (PLAN.md).
    - `delta` — `## Affects` / `## Delta` section + op-marker shape (proposal.md).
    - `scenarios` — Scenarios↔Acceptance mapping (SPEC.md, P2b).
    - `anchors` — Codebase Anchors module-resolve (SPEC.md, P2b).
    - `spec-semantic` — umbrella for `scenarios` + `anchors` over the same SPEC.md.
    - `plan-tasks` — slice↔acceptance mapping; reads paired `SPEC.md` next to PLAN.md (P2b).
    - `verified-deps` — `## Verified Dependencies` table shape on PLAN.md (P2b).
  - **Per-folder (positional `path` = feature folder)**:
    - `deviations` — cross-references `state.json` deviations against `decisions.md`.
  - **Repo-wide (no positional path; uses `--repo-root`)**:
    - `health` — D-HEALTH layout scan over `.forge/`.
    - `ship` — capability-uniqueness check (P2a slice; full ship-gate in P5).
    - `constitution` — Constitution structural check (defaults to `<repo-root>/.forge/CONSTITUTION.md` if `path` omitted).
    - `all` — fan-out: `health` + `ship` + per-feature semantic validators across `.forge/changes/` and `.forge/features/`.
- `--repo-root <path>` (defaults to cwd) for repo-wide targets and the `anchors` module-resolve base.
- `--check-registries` (off by default). When set, `verified-deps` (and `all`) probe `npm` / `pip` for declared dependencies.

> **Offline default for `verified-deps`:** by default `verified-deps` is shape-only; pass `--check-registries` for live registry probes (requires `npm` and/or `pip` on PATH). Keep it off in CI to stay deterministic.

## Steps

1. Parse args. If `--target` missing or unknown, exit 2 with a usage message.
2. Invoke `python -m tools.validate --target <target> [path] [--repo-root <root>]`.
3. The CLI prints structured JSON to stdout and a human summary to stderr.
4. If exit code is 0: print "No BLOCK findings." and exit.
5. If exit code is 1: print the human-readable findings (severity + file + message), grouped by severity. Surface remediation hints in the message text verbatim.
6. If exit code is 2: surface the usage error verbatim.

## Done

User sees structured findings (or "no findings" message). Skill never mutates state or files.

---
name: validate
description: Run the IDD structural validator across artifacts and surface findings. Use when the user asks to validate, check, or audit Constitution / delta / spec / repo health structure.
---

# /idd:validate

Run `tools/validate.py` and report findings. Read-only.

## Behavior

1. Parse args: required `--target <spec|plan|delta|constitution|ship|health|all>`, optional `path`, optional `--repo-root <path>`.
2. Invoke the `idd-validate` skill (see `skills/idd-validate/SKILL.md`).
3. Skill runs `tools/validate.py` and prints findings.
4. Exit code mirrors the underlying validator: `0` (no BLOCK / HIGH), `1` (any BLOCK or HIGH), `2` (usage error).

## P2a scope (current)

- `--target spec` runs structural checks only: NR placement + frontmatter schema.
- `--target plan` runs frontmatter schema only. Plan-task↔acceptance mapping ships in **P2b**.
- `--target delta` runs frontmatter schema + `## Affects` / `## Delta` section presence + op-marker presence.
- `--target constitution` runs the Constitution structural checker.
- `--target ship` runs capability-uniqueness only in P2a. Full ship-gate validation (Constitution acknowledge gate) lands in **P5**.
- `--target health` runs the D-HEALTH repo-wide scan.
- `--target all` is currently equivalent to `--target health`. **Deviation from M3 spec §5.3.6**: the spec language allows `all` to fan out across every P2a structural check (per-file constitution / delta / spec). P2a stages this to `health` only; per-file fan-out lands in P2b alongside the semantic checks (scenario↔acceptance mapping, plan task↔acceptance mapping). Recorded here so the deviation is visible at the command surface, not buried in code.

## Failure modes

- Unknown `--target` → exit 2 with usage message.
- Missing required `path` for per-file target (`spec`, `plan`, `delta`) → exit 1 with `BLOCK` finding (`--target X requires a path argument`).
- Positional `path` supplied with a repo-wide target → `WARN` finding noting the path was ignored.
- Malformed artifact → BLOCK finding listing the structural defect.

All errors surface verbatim from the underlying validator. No partial writes — read-only.

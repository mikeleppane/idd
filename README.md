# IDD — Intent-Driven Development

> Intent is the source. Spec is the contract. Verification reconciles reality.

A Claude Code plugin that encodes a lightweight-but-thorough Spec-Driven Development lifecycle.

## Status

M1 (Foundation + Focused Tier) — under active development.

## Lifecycle

```
refine → research → spec → domain → scenarios → plan → crucible → review → execute → verify → ship
```

## Tiers

| Tier | Phases |
|---|---|
| `--focused` | spec → execute → verify |
| `--standard` | spec → scenarios → plan → crucible → execute → verify → ship |
| `--full` | entire pipeline |

## Install (Claude Code)

_Coming after M1._ For now: clone this repo and reference it via your local plugin path.

## Use Outside Claude Code

`AGENTS.md` exposes the same skills and commands for Cursor, Aider, and Codex (verification deferred to a later milestone).

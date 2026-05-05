---
name: idd-validate
description: Run the structural validator over IDD artifacts and surface findings. Use when the user wants to check Constitution shape, delta proposals, NR placement, capability uniqueness, or the overall .idd/ health.
disable-model-invocation: true
---

# IDD Validate

## Goal

Run `tools/validate.py` against a target and surface findings to the user.

## Inputs

- `--target <spec|plan|delta|constitution|ship|health|all>` (required).
- Optional `path` argument for per-file targets (`spec`, `plan`, `delta`, `constitution`).
- `--repo-root <path>` (defaults to cwd) for repo-wide targets (`health`, `ship`, `all`).

## Steps

1. Parse args. If `--target` missing or unknown, exit 2 with a usage message.
2. Invoke `python -m tools.validate --target <target> [path] [--repo-root <root>]`.
3. The CLI prints structured JSON to stdout and a human summary to stderr.
4. If exit code is 0: print "No BLOCK findings." and exit.
5. If exit code is 1: print the human-readable findings (severity + file + message), grouped by severity. Surface remediation hints in the message text verbatim.
6. If exit code is 2: surface the usage error verbatim.

## Done

User sees structured findings (or "no findings" message). Skill never mutates state or files.

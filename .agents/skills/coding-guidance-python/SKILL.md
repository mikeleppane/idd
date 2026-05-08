---
name: coding-guidance-python
description: Write, modify, and review Python tooling for the FORGE plugin (state machine, frontmatter linter, schema validator) under strict ruff and mypy. Use when editing anything under tools/ or tests/, when adding a new tool module, or when reviewing a Python diff in this repo. The bar is high and small; this skill encodes it.
---

# Python Coding Guidance (FORGE)

FORGE is a Claude Code plugin written in markdown, with a small Python tooling library that the lifecycle skills shell out to. The Python surface is intentionally tiny: pure functions, sync I/O, frozen domain enums, schema-validated JSON. The codebase rewards code that reads like it was written once on purpose. This skill is the rulebook.

## Sources of truth, in order

1. `pyproject.toml` — ruff, mypy, pytest config. When this skill conflicts with `pyproject.toml`, the file wins.
2. `Makefile` — `make check` is the gate. `make lint && make typecheck && make test` must pass before a change is done.
3. This skill — opinionated rules, examples, anti-patterns.

## Project shape (Python only)

| Area | Path | Purpose |
| --- | --- | --- |
| State machine | `tools/state.py` | Read/validate/transition `state.json` |
| Frontmatter linter | `tools/lint_frontmatter.py` | YAML frontmatter quality + JSON Schema |
| Schema checker | `tools/check_schemas.py` | Planned (Task 12) |
| Test fixtures | `tests/conftest.py` | `repo_root`, `schemas_dir`, `templates_dir` |
| Schemas | `schemas/` | Draft 2020-12 JSON Schemas |
| Templates | `templates/feature/` | Feature scaffold |

Modules stay small and flat. There is no `tools/state/__init__.py` package; there is one file per cohesive unit. Resist the urge to grow a tree.

## Stack

- Python 3.12+. No older.
- Sync everywhere. No `asyncio`, no `await`. If you find yourself reaching for it, you are working on the wrong project.
- Runtime deps: `jsonschema>=4.21`, `PyYAML>=6.0`, `rfc3339-validator`.
- Dev deps: `ruff>=0.15` (strict), `mypy>=1.20` (strict), `pytest>=9.0`.
- Install with plain `pip install -e ".[dev]"` from a venv. Nothing fancier.

## Workflow

1. **Read first.** The module you are touching, its tests, the schemas it depends on, and the relevant section of `docs/specs/2026-05-02-forge-design.md`.
2. **Pick the narrowest change** that keeps contracts, error types, and public function signatures explicit.
3. **Tighten types as you go.** Never loosen. If mypy complains, the code is wrong, not mypy.
4. **Update or add tests** in the same commit. See `../test-driven-development/SKILL.md` for the test discipline; this skill governs the production code.
5. **Run `make check`.** Lint, types, tests — all must be clean. If a hook fails, fix the root cause and create a new commit (see `../git-conventions/SKILL.md`).

---

## The rules

### Module preamble

Every module starts the same way:

```python
"""One-line module docstring describing what lives here."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
```

- `from __future__ import annotations` is non-negotiable. PEP 563 keeps annotations as strings, which keeps modern type expressions cheap and forward references painless.
- Imports are grouped stdlib / third-party / local, sorted by ruff `I`. Don't hand-order; let the formatter do its job.

### Types are the documentation that runs

Annotate every function. Mypy strict + `disallow_untyped_defs` will reject anything else, but the spirit is more important than the rule: if the signature is honest about what goes in and what comes out, the body usually writes itself.

```python
def feature_folder_exists(repo_root: Path, feature_id: str) -> bool:
    """Return True when .forge/features/<feature_id>/ exists under repo_root."""
    return (repo_root / ".forge" / "features" / feature_id).is_dir()
```

That signature tells the caller everything: it takes a `Path` and an id, returns a bool, has no side effects, and the docstring confirms the predicate. No `Optional` hedge, no `Any`, no comment explaining what `True` means.

### Domain enums are tuples, not loose strings

The canonical "make illegal states unrepresentable" pattern in FORGE:

```python
VALID_LIFECYCLE_PHASES = (
    "refine", "research", "spec", "domain", "scenarios", "plan",
    "crucible", "review", "execute", "verify", "ship",
)


def start_phase(path: Path, phase: str, ...) -> dict[str, Any]:
    if phase not in VALID_LIFECYCLE_PHASES:
        raise StateError(f"unknown phase '{phase}'; must be one of {VALID_LIFECYCLE_PHASES}")
    ...
```

A `tuple[str, ...]` at module scope is immutable, hashable, iterable, and prints nicely in error messages. Reach for `Literal` only when mypy needs to reason about specific values; the runtime guard catches everything else.

For private closed sets that drive validators, use `frozenset`:

```python
_IMPERATIVE_BLOCKLIST = frozenset({
    "this", "the", "a", "an", "skill", "command", "it",
    "helps", "allows", "enables", "lets", "permits",
})
```

`frozenset` says "this set is closed and lookups are O(1)". A list would lie about both.

### Domain errors are named `RuntimeError` subclasses

Every layer that can fail in a domain-specific way gets a named exception:

```python
class StateError(RuntimeError):
    """Raised when state.json cannot be read, parsed, or transitioned."""


class FrontmatterError(RuntimeError):
    """Raised when frontmatter cannot be parsed or fails schema validation."""
```

Rules:

- One line, one role. The class docstring is the contract.
- `RuntimeError` subclass — these are runtime conditions, not programmer errors (`ValueError`, `TypeError`).
- Always include actionable context in the message: the path that failed, the phase that was unknown, the field that was wrong. `f"state.json at {path} is invalid JSON: {exc}"` beats `"invalid state"`.
- Always chain with `from`:

  ```python
  try:
      payload = json.loads(path.read_text(encoding="utf-8"))
  except json.JSONDecodeError as exc:
      raise StateError(f"state.json at {path} is invalid JSON: {exc}") from exc
  ```

  `from exc` preserves the cause and the traceback. Without it the original failure is gone forever.
- Never `except Exception:` outside a `main()` boundary. Catch the specific type or don't catch at all.

### Schema validation is a function, not a framework

`jsonschema.Draft202012Validator` with a format checker is the canonical seam:

```python
def _validator_for(schema: dict[str, Any]) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )


def read_state(path: Path, schema_path: Path | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if schema_path is not None:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = _validator_for(schema)
        errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
        if errors:
            messages = "; ".join(e.message for e in errors)
            raise StateError(f"state.json at {path} fails schema: {messages}")
    return payload
```

Notes on the pattern:

- The schema is data, loaded from disk. It is not a Python class. Schemas live in JSON, validators live in `jsonschema` — that is deliberate.
- `format_checker=...` activates RFC 3339 enforcement via `rfc3339-validator`. Without it, `format: date-time` is decorative.
- `iter_errors` returns every problem; don't fail on the first. Sort by JSON path for stable output and join into a single message.
- Validate **before** writing. `write_state` validates and refuses to write on failure — the file on disk is never half-correct.

### `pathlib.Path` only

Ruff `PTH` enforces this. There is no excuse for `os.path` in 2026. Build paths with `/`:

```python
features_dir = repo_root / ".forge" / "features" / feature_id
schema_path = schemas_dir / "state.schema.json"
```

Read and write through `Path` methods, always with explicit encoding:

```python
text = path.read_text(encoding="utf-8")
path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
```

`encoding="utf-8"` is a quiet bug fix on Windows and on systems where `LANG` is not what you think.

### Google docstrings on the public surface

Public functions get full docstrings — what it does, args, returns, raises:

```python
def complete_phase(
    path: Path,
    phase: str,
    schema_path: Path | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Mark `phase` done with completed_at timestamp. Persist and return new state.

    `current_phase` is NOT changed; call `start_phase` next to move forward.

    Args:
        path: state.json path.
        phase: Lifecycle phase name to complete.
        schema_path: Optional schema for read+write validation.
        now: Optional ISO 8601 timestamp; defaults to UTC now.

    Returns:
        Updated state payload.

    Raises:
        StateError: Unknown phase, missing entry, or schema failure.
    """
```

Private helpers (underscore-prefixed) are exempt from `D` rules — keep them pithy or omit the docstring entirely. Tests are exempt from `D` and `S101` and `PLR2004` per `pyproject.toml`. Don't fight the config.

### First-tier bug-causers

These are the four mistakes most likely to bite you in this codebase. Internalize them.

**1. Mutable default arguments.** One list, shared across every call.

```python
# Wrong — every caller mutates the same list
def collect(items: list[str] = []) -> list[str]:
    items.append("phase")
    return items

# Right — None sentinel, construct inside
def collect(items: list[str] | None = None) -> list[str]:
    items = list(items) if items is not None else []
    items.append("phase")
    return items
```

**2. `==` vs `is` on `None`/sentinels.** Identity, not equality.

```python
if schema_path is not None:        # right
    ...
if schema_path != None:            # wrong; ruff flags it, mypy may not
    ...
```

Same goes for `is True` / `is False` — almost always wrong, almost always a sign of a bool that should be a richer type.

**3. Swallowed exceptions.** `except:` and `except Exception: pass` are how bugs become silent.

```python
# Wrong — hides every failure including KeyboardInterrupt
try:
    payload = json.loads(text)
except:
    payload = {}

# Right — name the cause, translate to a domain error, preserve context
try:
    payload = json.loads(text)
except json.JSONDecodeError as exc:
    raise StateError(f"invalid JSON at {path}: {exc}") from exc
```

**4. Off-by-one on frontmatter / line indexing.** The frontmatter parser walks `lines[1:]` after consuming the opening `---`. Test the boundary cases:

- File with no frontmatter at all (`return None`).
- File whose only line is `---` (unclosed; raise).
- File with frontmatter but empty body (closed; parse to `{}` or `None`).

These edges are where the bug lives. Cover them.

### CLI entry points

Tools that ship as executables follow the same shape:

```python
def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Lint each path; return 0 when all valid, 1 otherwise.

    Args:
        argv: Optional argv override (defaults to sys.argv).

    Returns:
        Exit code: 0 on success, 1 on any validation failure.
    """
    parser = argparse.ArgumentParser(description="Lint FORGE skill/command frontmatter.")
    parser.add_argument("--schema", required=True, type=Path)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)

    rc = 0
    for path in args.paths:
        for err in validate_file(path, args.schema):
            print(err, file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
```

Conventions:

- `main(argv: list[str] | None = None) -> int` — testable. The test passes a list; production passes nothing.
- Exit codes via `raise SystemExit(main())`. Return ints from `main`, not strings.
- `argparse` with `type=Path` — the parser does the conversion; downstream code never sees a string for a path.
- Errors go to `stderr`, results go to `stdout`. Don't mix them.

### No commented-out code

Ruff `ERA` enforces this. Dead code goes; `git log` is the archive. If you genuinely need to keep an alternative implementation visible, write a docstring or a `# Note:` comment that explains *why* — not the code itself.

### Constants and naming

- Module-level constants use `UPPER_SNAKE` for public (`VALID_LIFECYCLE_PHASES`) and `_UPPER_SNAKE` for private (`_IMPERATIVE_BLOCKLIST`, `_USE_WHEN_PATTERN`, `_FENCE`).
- Functions use `snake_case`. `_leading_underscore` for module-private.
- `frozenset({...})` for closed string sets, `tuple(...)` for ordered enums, `re.compile(..., re.IGNORECASE)` at module scope for regexes used across calls — compile once, match many.

### Module-boundary discipline

Small flat modules over deep packages.

- `tools/state.py` is one file. It exports `StateError`, `read_state`, `write_state`, `start_phase`, `complete_phase`, `finish_feature`, `feature_folder_exists`, `VALID_LIFECYCLE_PHASES`. Nothing more.
- `tools/lint_frontmatter.py` is one file. The `_description_quality_errors` helper is private; promote it only when a second caller appears.
- New tools start as a single file. Split into a package (`tools/foo/__init__.py` + siblings) only when the file grows past ~400 lines or has clearly separable concerns. Premature packaging is a sin.

Cross-module imports stay shallow. `tools/check_schemas.py` should not import from `tools/state.py` unless it genuinely needs the validator helper — and if it does, that helper should be promoted to a shared spot, not reached for through the side door.

### What this codebase does not use

If you find yourself typing one of these, stop and ask whether you're in the right repo:

- Data-validation libraries that wrap JSON Schema — schemas live in JSON, validators live in `jsonschema`. Nothing more.
- Anything from the async stack — `asyncio`, `await`, async HTTP or MQTT clients. FORGE is sync.
- Structured-logging frameworks or stdlib `logging` configured at module scope — CLI tools print, tests assert. Add logging only when there's a clear caller asking for it.
- `os.path`, `os.getcwd()` — `pathlib.Path` and explicit args.
- `Protocol` + dependency injection — FORGE tools are pure functions; pass values, not seams.
- `subprocess` with `shell=True` — ruff `S` will reject it. Always the list form, always `check=True`.

---

## Decision heuristics

| Signal | Move |
| --- | --- |
| Function over ~40 lines | Extract a helper; the function probably has two jobs. |
| More than 5 parameters | Group into a small frozen dataclass or split the function. |
| Nesting > 3 levels | Early returns, then extract. |
| `Any` showing up in a public signature | Stop. Find the type. `dict[str, Any]` for opaque JSON payloads is the only acceptable case, and even then keep it at the boundary. |
| Touching > 3 modules in one change | Stop and re-plan. The change is bigger than it looks. |
| Reaching for a third-party dep | Check `pyproject.toml`. If it isn't there, the bar is high — does the stdlib + jsonschema + PyYAML actually fail to cover this? |
| Considering async | You are not. FORGE is sync. |

## Anti-patterns (with why)

```python
# Wrong: bare except eats KeyboardInterrupt and SystemExit
try:
    payload = read_state(path)
except:                                  # noqa - illustrative
    payload = {}
# Right: name the failure mode, translate, chain
try:
    payload = read_state(path)
except StateError as exc:
    raise FeatureBootstrapError(f"cannot bootstrap: {exc}") from exc
```

```python
# Wrong: string concatenation for paths
schema_path = schemas_dir + "/state.schema.json"
# Right: pathlib does separators, normalization, and Windows for free
schema_path = schemas_dir / "state.schema.json"
```

```python
# Wrong: stringly-typed phase, no guard
def start_phase(path, phase):
    payload = read_state(path)
    payload["current_phase"] = phase     # any string sneaks through
    write_state(path, payload)

# Right: typed signature + tuple membership guard + named error
def start_phase(path: Path, phase: str, ...) -> dict[str, Any]:
    if phase not in VALID_LIFECYCLE_PHASES:
        raise StateError(f"unknown phase '{phase}'; must be one of {VALID_LIFECYCLE_PHASES}")
    ...
```

```python
# Wrong: validate-after-write leaves bad data on disk on the next bug
def write_state(path, payload):
    path.write_text(json.dumps(payload))
    validate(payload)  # too late if validate raises

# Right: validate first, write only on success
def write_state(path: Path, payload: dict[str, Any], schema_path: Path | None = None) -> None:
    if schema_path is not None:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = _validator_for(schema)
        errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
        if errors:
            messages = "; ".join(e.message for e in errors)
            raise StateError(f"refusing to write: payload fails schema: {messages}")
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
```

---

## Reviewing a Python diff

When the task is review rather than implementation, walk these axes in order. Report findings by severity (Critical, Important, Nit).

1. **Correctness.** Does the code do what the docstring promises? Are the `raises` accurate? Are edge cases (empty input, missing file, unclosed fence) covered?
2. **Type honesty.** Any new `Any`? Any silent `# type: ignore` without a reason code and a comment? Any signature that lies about what it returns?
3. **Error handling.** Domain error or generic `RuntimeError`? `from exc` present? Message actionable? Catch scope as narrow as possible?
4. **Module boundaries.** Did a helper grow into a second concern? Should something be promoted from private to public, or vice versa?
5. **Tests.** Is the new behavior covered? Are the edge cases there? Tests use `assert` (`S101` exempt) and skip `D` rules — that's fine. Tests should not catch broad exceptions any more than production code does.
6. **Quality gate.** `make check` passes locally. No new ruff or mypy noise.

Cross-references: `../test-driven-development/SKILL.md` for the test discipline, `../git-conventions/SKILL.md` for commit shape.

---

## Done means

- `make lint` clean.
- `make typecheck` clean — no new `# type: ignore`, no widened `Any`.
- `make test` green; new behavior has a test, edge cases included.
- No commented-out code, no dead imports, no `os.path`.
- Public functions have google docstrings; private helpers stay pithy.
- Domain errors named, chained with `from`, messages include actionable context.
- Schemas validated before writes; the file on disk is never half-correct.
- Commit message explains the *why*.

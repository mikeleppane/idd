---
name: test-driven-development
description: Drive every behavioral change in FORGE through a failing test first. Use when adding or modifying anything in `tools/` (state machine, frontmatter linter, schema checker), changing JSON Schemas, fixing a bug surfaced by the focused-tier flow, or when `/forge:execute` dispatches an implementer subagent. Write the failing test before the code; use the Prove-It pattern for bugs; assert on observable state of `Path` payloads and dict outputs, not on internals. `make check` is the floor before claiming done.
---

# Test-Driven Development (FORGE)

Write the failing test before the code. For bug fixes, reproduce the bug with a test *before* attempting to fix it. Tests are proof — "seems right" is not done. FORGE is itself a methodology for driving development through specs and verification; the tooling that encodes that methodology has to hold itself to the same bar.

This skill covers the *process* of driving development through tests and the *shape* of a good FORGE test. It does **not** restate the repo's production-code rules — when a rule is about how the code under test must be written (typing, `pathlib.Path`, docstrings, line length), cite [coding-guidance-python](../coding-guidance-python/SKILL.md) rather than duplicate. Commit hygiene around bug-fix tests lives in [git-conventions](../git-conventions/SKILL.md).

Project-specific rules live in [AGENTS.md](../../../AGENTS.md) and override anything here.

---

## FORGE testing conventions — source of truth

Re-read AGENTS.md "Testing" before every test commit. The highlights:

- **`make check` is the floor.** It runs `make lint`, `make typecheck`, and `make test`. All three must pass before you declare any change done. There is no separate `unit` / `component` split today — `make test` runs the whole suite.
- **No async.** FORGE tooling is synchronous. No async pytest plugin configuration, no `@pytest.mark.asyncio`, no `asyncio.timeout`, no `TaskGroup`. If you reach for those, the design is wrong.
- **Tests get full type annotations.** mypy strict applies to `tests/` too — `def test_…() -> None:` is the minimum, and fixtures get typed return types.
- **Tests are exempt from `D` rules.** Docstrings are not required on test functions; keep them terse and let the name carry the contract.
- **`tmp_path` for any filesystem work.** Never write under `/tmp/`, the cwd, or anywhere outside the per-test temp directory.
- **Test layout mirrors `tools/`.** Tests for `tools/state.py` live in `tests/tools/test_state.py`. Cross-cutting smoke tests go under `tests/smoke/`.
- **Fixtures live in `tests/conftest.py`.** The standard ones today are `repo_root`, `schemas_dir`, `templates_dir`. Reuse them; don't reinvent paths in each file.

A note on markers: `pyproject.toml` declares `markers = ["unit: fast, isolated", "smoke: end-to-end against fixture"]`, but **M1 does not enforce them** with `--strict-markers`. Most M1 tests are plain unit tests with no marker. Don't sprinkle markers preemptively; add `pytest.mark.smoke` only when a test genuinely walks a focused-tier scenario end-to-end against a fixture feature folder.

---

## When to use TDD

- Adding or changing any function in `tools/` — state machine transitions, frontmatter parsing, schema checks, anything an `/forge:*` command will call.
- Tightening a JSON Schema (`schemas/state.schema.json`, `schemas/frontmatter.schema.json`, `schemas/spec-frontmatter.schema.json`) — the schema test is the proof that the new constraint rejects the bad input.
- Fixing any bug — the **Prove-It pattern** below is the default. No fix without a failing test first.
- Modifying existing behavior in a way the lifecycle observes (a new phase entry, a new error class, a renamed state field) — change the test first so the regression is caught if the change is later reverted.
- Implementing a new tier (`--focused`, `--standard`, `--full`) flow — the smoke test asserts the lifecycle reaches the expected phase against a fixture feature.

## When *not* to use TDD

- Pure documentation edits (`docs/specs/**`, `docs/plans/**`, `README`) — no runtime behavior.
- Skill or command markdown edits in `.agents/skills/**` and `commands/**` — these are prose contracts; the verification is the next agent reading them.
- Renaming a private symbol where mypy + ruff give equivalent proof, and no test references the old name.
- Scaffolding an empty module that nothing calls yet — add the test when the first real call site lands.

If unsure, lean toward writing the test. Writing a test you later delete is cheap; shipping a behavior change with no test is expensive.

---

## The TDD cycle

```
    RED                 GREEN               REFACTOR
 Write a test     Write minimal code     Clean up the
 that fails  ───→  to make it pass  ───→  implementation  ───→  (repeat)
      │                   │                      │
      ▼                   ▼                      ▼
   Test FAILS         Test PASSES          Tests still PASS
```

### Step 1 — RED: write the failing test

A test that passes on the first run proves nothing. It usually means the test asserts on something already true (default value, file the fixture already wrote, schema field that didn't exist) or the code you intended to write already existed under another name. **If RED doesn't go red, stop and figure out why.**

```python
# tests/tools/test_state.py
from __future__ import annotations

from pathlib import Path

import pytest

from tools.state import StateError, start_phase


def test_start_phase_rejects_phase_outside_lifecycle(tmp_path: Path) -> None:
    feature = tmp_path / "2026-05-04-example"
    feature.mkdir()
    (feature / "STATE.json").write_text('{"phase": "refine", "history": []}')

    with pytest.raises(StateError, match="not a valid lifecycle phase"):
        start_phase(feature, "frobnicate")
```

At this point either `start_phase` doesn't validate against `VALID_LIFECYCLE_PHASES`, or it raises a different error class. Run `pytest tests/tools/test_state.py::test_start_phase_rejects_phase_outside_lifecycle`. The test must fail. Good — that's RED confirmed.

### Step 2 — GREEN: minimum code to pass

Resist the urge to design the whole module. Pass *this* test.

```python
# tools/state.py
from __future__ import annotations

from pathlib import Path

VALID_LIFECYCLE_PHASES: frozenset[str] = frozenset(
    {
        "refine",
        "research",
        "spec",
        "domain",
        "scenarios",
        "plan",
        "crucible",
        "review",
        "execute",
        "verify",
        "ship",
    },
)


class StateError(Exception):
    """Raised when a state transition or read is invalid."""


def start_phase(feature_folder: Path, phase: str) -> None:
    """Begin `phase` for the feature at `feature_folder`.

    Args:
        feature_folder: Path to the feature's folder under ``docs/features/``.
        phase: Name of the lifecycle phase to enter.

    Raises:
        StateError: If ``phase`` is not in :data:`VALID_LIFECYCLE_PHASES`.
    """
    if phase not in VALID_LIFECYCLE_PHASES:
        raise StateError(f"{phase!r} is not a valid lifecycle phase")
    # ... remaining transition logic driven by the next test
```

That's it. No history rewrite, no `current_slice` field, no "might be useful later" hooks. **The next test drives the next piece of the design** — not speculation.

### Step 3 — REFACTOR: clean up under green

With the test passing, improve without changing observable behavior. Run `make test` after *each* refactor step, not at the end.

- Extract helpers when duplication appears (third use, not first).
- Replace a bare `str` with a `Literal` or a `NewType` when the domain justifies it (e.g. `Phase = Literal["refine", "research", ...]`).
- Move a constant into the module that owns the concept (validators next to `VALID_LIFECYCLE_PHASES`).
- Tighten return types — return a typed dict or a frozen dataclass instead of a loose `dict[str, object]`.

If the refactor reveals that the test was too implementation-coupled (e.g. "this refactor should be invisible but it broke three tests"), the tests are the problem — fix them to assert on behavior, not plumbing. See *Test state, not interactions* below.

---

## The Prove-It pattern (bug fixes)

When a bug is reported, the first thing you write is **not the fix**. It is a test that reproduces the bug against the current code and fails.

```
Bug report
     │
     ▼
Write a test that reproduces the observed failure
     │
     ▼
Test FAILS → bug confirmed (and you now understand it)
     │
     ▼
Implement the fix
     │
     ▼
Test PASSES → fix works
     │
     ▼
make check → no regressions
```

Skipping the reproduction test is how "fixes" that don't fix anything get merged. If the test was already there, it would have caught the bug. The Prove-It test is the guard that prevents the same bug from coming back silently in six months.

**Rule:** every bug-fix commit includes a test that would fail against `HEAD~1`. No test, no merge. In the commit message body (see [git-conventions](../git-conventions/SKILL.md)), name the symptom and how the test proves it.

### Example

Bug report: "Re-entering a phase via `start_phase` leaves the previous phase's `current_slice` field in `STATE.json`. The next tool call sees a stale slice and dispatches the wrong subagent."

```python
# tests/tools/test_state.py
from __future__ import annotations

import json
from pathlib import Path

from tools.state import start_phase


def test_start_phase_clears_current_slice_when_re_entering_phase(tmp_path: Path) -> None:
    feature = tmp_path / "2026-05-04-example"
    feature.mkdir()
    state = {
        "phase": "execute",
        "history": ["execute"],
        "current_slice": "slice-2",
    }
    (feature / "STATE.json").write_text(json.dumps(state))

    start_phase(feature, "execute")

    after = json.loads((feature / "STATE.json").read_text())
    assert "current_slice" not in after
```

Run it: `FAILED — assert 'current_slice' not in {'phase': 'execute', ..., 'current_slice': 'slice-2'}`. Bug confirmed. Now fix `start_phase` to drop the slice field on re-entry. Re-run: passes. Run `make check`: clean. Now the same input that caused the incident is a test-guarded failure.

---

## Writing good tests

### Test state, not interactions

Assert on the *outcome* on disk and in returned values, not on which internal helpers got called. Interaction tests calcify the implementation — any refactor that preserves behavior breaks the test, which trains authors to update tests blindly until they stop catching anything.

```python
# Good — asserts on observable state (the JSON file the tool wrote)
def test_complete_phase_appends_phase_to_history(tmp_path: Path) -> None:
    feature = tmp_path / "2026-05-04-example"
    feature.mkdir()
    (feature / "STATE.json").write_text('{"phase": "spec", "history": ["refine", "research"]}')

    complete_phase(feature, "spec")

    after = json.loads((feature / "STATE.json").read_text())
    assert after["history"] == ["refine", "research", "spec"]


# Bad — asserts on how it's done, not what it does
def test_complete_phase_calls_write_state(tmp_path: Path, mocker: MockerFixture) -> None:
    spy = mocker.spy(state_module, "write_state")
    complete_phase(tmp_path, "spec")
    assert spy.call_count == 1  # breaks the moment we batch writes or rename
```

### DAMP over DRY in tests

In production code, DRY usually wins. In tests, **DAMP — Descriptive And Meaningful Phrases — wins.** A test should read like a spec on its own, without the reader tracing through shared helpers.

```python
# DAMP — each test is self-contained and reads as a specification
def test_validate_file_rejects_frontmatter_with_unknown_field(tmp_path: Path) -> None:
    path = tmp_path / "SPEC.md"
    path.write_text("---\ntitle: x\nphase: spec\nbogus: 1\n---\n\nbody\n")

    with pytest.raises(FrontmatterError, match="unknown field: bogus"):
        validate_file(path)


def test_validate_file_rejects_frontmatter_missing_required_phase(tmp_path: Path) -> None:
    path = tmp_path / "SPEC.md"
    path.write_text("---\ntitle: x\n---\n\nbody\n")

    with pytest.raises(FrontmatterError, match="missing required field: phase"):
        validate_file(path)
```

Duplication is fine. Each test failure tells you exactly what broke without a hunt through fixtures. Extract a helper only when the *noise* exceeds the *signal* — usually when the setup dwarfs the assertion.

### Prefer real > fake > stub > mock

The more of your test runs real code, the more confidence it gives. Preference order (most → least):

1. **Real implementation.** First choice. Use the actual `parse_frontmatter`, the actual `jsonschema.Draft202012Validator`, the actual `read_state` reading a real file under `tmp_path`. FORGE tooling is fast and deterministic — there's almost never a reason not to use the real thing.
2. **Fake.** A small in-memory replacement that implements the same contract. Rare in FORGE because most code is pure functions on `Path` + dict. If you need one, write a ~10-line concrete class right next to the test (or in the nearest `conftest.py`); don't reach for `unittest.mock`.
3. **Stub.** A canned input — a fixture file under `tests/fixtures/` for the smoke test, a hand-built dict for a unit test.
4. **Mock (interaction verification).** Use **only** when the behavior under test is *about the interaction itself*. In FORGE that almost never applies. If you find yourself mocking `pathlib.Path` or `json.dumps`, stop — the test is wrong.

**Over-mocking is the #1 cause of tests that pass while production breaks.** If your test file imports `unittest.mock`, there should be a specific reason — and it belongs in a comment or the commit body. There is no "named seams" / `Protocol` injection layer in FORGE; tools are pure functions on `Path` and dict payloads. When a tool needs an external location (e.g. a schema directory), inject it as a `Path` parameter and pass the `schemas_dir` fixture in the test.

### Arrange-Act-Assert

```python
def test_finish_feature_marks_state_as_shipped(tmp_path: Path) -> None:
    # Arrange
    feature = tmp_path / "2026-05-04-example"
    feature.mkdir()
    (feature / "STATE.json").write_text('{"phase": "ship", "history": ["ship"]}')

    # Act
    finish_feature(feature)

    # Assert
    after = json.loads((feature / "STATE.json").read_text())
    assert after["phase"] == "shipped"
```

One block per step. If the "Act" has more than one meaningful call, the test is probably testing two things — split it.

### One assertion per concept

```python
# Good — one concept per test, failure tells you exactly what regressed
def test_read_state_raises_state_error_when_file_missing(tmp_path: Path) -> None:
    with pytest.raises(StateError, match="STATE.json not found"):
        read_state(tmp_path / "missing")


def test_read_state_raises_state_error_on_invalid_json(tmp_path: Path) -> None:
    feature = tmp_path / "f"
    feature.mkdir()
    (feature / "STATE.json").write_text("{not json")
    with pytest.raises(StateError, match="invalid JSON"):
        read_state(feature)


# Bad — one test, two concepts, first failure hides the second
def test_read_state_errors(tmp_path: Path) -> None:
    with pytest.raises(StateError):
        read_state(tmp_path / "missing")
    feature = tmp_path / "f"
    feature.mkdir()
    (feature / "STATE.json").write_text("{not json")
    with pytest.raises(StateError):
        read_state(feature)
```

**Parameterize** when the shape is identical across inputs (`@pytest.mark.parametrize`). Don't parameterize when the *reasoning* differs — separate tests read better.

```python
@pytest.mark.parametrize(
    "phase",
    ["refine", "research", "spec", "domain", "scenarios", "plan", "crucible", "review", "execute", "verify", "ship"],
)
def test_start_phase_accepts_every_lifecycle_phase(tmp_path: Path, phase: str) -> None:
    feature = tmp_path / "f"
    feature.mkdir()
    (feature / "STATE.json").write_text('{"phase": "refine", "history": []}')

    start_phase(feature, phase)  # contract is identical for every phase
```

### Name tests as sentences

`test_<unit>_<scenario>_<expected_behavior>` — the name reads as a sentence in failure output.

```python
# Good — failure output reads as a spec
def test_read_state_returns_dict_with_phase_and_history_keys() -> None: ...
def test_parse_frontmatter_strips_leading_and_trailing_whitespace() -> None: ...
def test_validate_file_emits_line_number_for_each_violation() -> None: ...

# Bad — tells you nothing when it fails
def test_state() -> None: ...
def test_parser_works() -> None: ...
def test_error_handling() -> None: ...
```

If you can't name the test as a sentence, you probably don't know yet what behavior you're asserting.

---

## Testing JSON Schemas

FORGE's contracts live in `schemas/`. Validate them with the real `jsonschema` library against representative payloads — never re-implement the rule in the test.

```python
# tests/tools/test_state_schema.py
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest


def test_state_schema_rejects_state_with_unknown_phase(schemas_dir: Path) -> None:
    schema = json.loads((schemas_dir / "state.schema.json").read_text())
    payload = {"phase": "frobnicate", "history": []}

    with pytest.raises(jsonschema.ValidationError, match="phase"):
        jsonschema.Draft202012Validator(schema).validate(payload)


def test_state_schema_accepts_minimal_valid_state(schemas_dir: Path) -> None:
    schema = json.loads((schemas_dir / "state.schema.json").read_text())
    payload = {"phase": "refine", "history": []}

    jsonschema.Draft202012Validator(schema).validate(payload)  # no exception
```

Two patterns to lean on:

- **One test per rejection cause.** Don't bundle "rejects unknown phase" and "rejects missing history" into one test; failure output should name the violated rule.
- **Use the `schemas_dir` fixture** from `tests/conftest.py`. Never hardcode `Path("schemas")` — the test must work regardless of the cwd.

---

## Test scaffolding — `tests/conftest.py`

Tests accumulate scaffolding. Where it lives matters — unconstrained fixture files become soup, and soup hides what each test actually asserts.

`tests/conftest.py` is the home for cross-cutting fixtures. Today the canonical set is:

```python
# tests/conftest.py
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    """Absolute path to the repository root."""
    return Path(__file__).resolve().parent.parent


@pytest.fixture
def schemas_dir(repo_root: Path) -> Path:
    """Absolute path to the `schemas/` directory."""
    return repo_root / "schemas"


@pytest.fixture
def templates_dir(repo_root: Path) -> Path:
    """Absolute path to the `templates/` directory."""
    return repo_root / "templates"
```

Rules of the road:

- **Reuse `repo_root`, `schemas_dir`, `templates_dir`.** Don't recompute them with `Path(__file__)` arithmetic in individual tests.
- **Prefer builder functions over fixtures for payload construction.** Fixtures hide inputs; a builder makes them explicit.

  ```python
  def make_state(phase: str = "refine", history: list[str] | None = None) -> dict[str, object]:
      """Build a minimal valid STATE.json payload."""
      return {"phase": phase, "history": history or []}


  def test_complete_phase_appends_to_history(tmp_path: Path) -> None:
      state = make_state(phase="spec", history=["refine", "research"])
      (tmp_path / "STATE.json").write_text(json.dumps(state))
      ...
  ```

- **No import-time side effects in `conftest.py`** — it runs under `pytest --collect-only` too. No filesystem reads, no module-level globals that touch the disk. Same rule as production modules ([coding-guidance-python](../coding-guidance-python/SKILL.md)).

---

## FORGE-specific patterns

### Filesystem under test — `tmp_path`, always

```python
def test_write_state_creates_state_json_with_compact_separators(tmp_path: Path) -> None:
    write_state(tmp_path, {"phase": "refine", "history": []})

    raw = (tmp_path / "STATE.json").read_text()
    assert raw == '{"phase":"refine","history":[]}\n'
```

Never write to `/tmp/…`, `~/.cache/…`, or a relative path — pytest workers share the filesystem and trash each other. `tmp_path` is per-test, cleaned automatically.

### Smoke tests against a fixture feature folder

When a change spans multiple tools (e.g. `start_phase` → `complete_phase` → `finish_feature`), a smoke test under `tests/smoke/` proves the focused tier walks end-to-end. Mark these with `pytest.mark.smoke` so future filtering is cheap.

```python
# tests/smoke/test_focused_tier_smoke.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.state import complete_phase, finish_feature, start_phase, write_state

pytestmark = pytest.mark.smoke


def test_focused_tier_walks_refine_through_ship(tmp_path: Path) -> None:
    feature = tmp_path / "2026-05-04-fixture"
    feature.mkdir()
    write_state(feature, {"phase": "refine", "history": []})

    for phase in ("refine", "spec", "plan", "execute", "verify", "ship"):
        start_phase(feature, phase)
        complete_phase(feature, phase)
    finish_feature(feature)

    final = json.loads((feature / "STATE.json").read_text())
    assert final["phase"] == "shipped"
    assert final["history"] == ["refine", "spec", "plan", "execute", "verify", "ship"]
```

The fixture feature folder is constructed under `tmp_path`. Don't reach into `docs/features/` for a real feature — that couples the test to a moving target.

### Schema and template paths — fixtures only

When a tool needs to locate `schemas/state.schema.json` or `templates/feature/SPEC.md`, **inject the path as a parameter**. Production callers pass the real path; tests pass the `schemas_dir` / `templates_dir` fixture. No `os.environ` lookups, no module-level constants that point at the repo root.

```python
def check_state_against_schema(state_path: Path, schema_path: Path) -> None:
    """Validate `state_path` against the JSON Schema at `schema_path`."""
    schema = json.loads(schema_path.read_text())
    payload = json.loads(state_path.read_text())
    jsonschema.Draft202012Validator(schema).validate(payload)


def test_check_state_against_schema_passes_for_valid_state(
    tmp_path: Path, schemas_dir: Path,
) -> None:
    state_path = tmp_path / "STATE.json"
    state_path.write_text('{"phase":"refine","history":[]}')

    check_state_against_schema(state_path, schemas_dir / "state.schema.json")
```

### Time and randomness — pass them as parameters

If a tool needs the current date (e.g. to stamp `2026-05-04-feature-name` on a folder), take a `today: date` parameter with a sensible production default. Tests pass a fixed `date(2026, 5, 4)`. Don't patch `datetime.date.today()`; don't mock `datetime`. The discipline is the same as schemas: external input is a parameter, not a hidden dependency.

---

## Test anti-patterns in FORGE

| Anti-pattern | Why it hurts | Do this instead |
|---|---|---|
| `@pytest.mark.asyncio` or async pytest plugin config | FORGE is sync; the marker is meaningless and signals a wrong design. | Delete the decorator. If you genuinely need async, raise it as a design question first. |
| `unittest.mock.patch("tools.state.…")` | Patches through the module system; breaks on rename; couples test to import path. | Inject the dependency as a `Path` / dict parameter and pass it from the test. |
| Hardcoded `Path("schemas/state.schema.json")` in a test | Breaks when pytest is run from a different cwd; couples the test to repo layout. | Use the `schemas_dir` fixture. |
| Reaching into `docs/features/<real feature>/` from a test | Couples the test to a moving target; flakes when the real feature evolves. | Build a fixture feature folder under `tmp_path`. |
| Re-implementing JSON Schema rules in Python inside the test | The rule under test is the schema; an inline reimplementation tests nothing. | Use `jsonschema.Draft202012Validator(schema).validate(payload)`. |
| Asserting on `mock.call_count == N` | Implementation-coupled; refactors break green tests. | Assert on the JSON the tool wrote, the dict it returned, the exception it raised. |
| `unittest.mock.Mock()` for a pure function on `Path` | There's nothing to mock — the function takes a path and returns a value. | Call the real function with a `tmp_path` argument. |
| `time.sleep(…)` in a test | FORGE is sync; nothing to wait for. Flakiness disguised as patience. | Remove the sleep. If a test feels racy, the design is wrong. |
| Snapshot tests on whole `STATE.json` files | Nobody reviews the snapshot; breaks on harmless field-order churn. | Assert on the specific keys the behavior cares about. |
| Tests that pass on first run without you verifying they fail first | Test may be asserting on a default or a file the fixture already wrote. | Always confirm RED before GREEN. |
| `test_it_works` / `test_state` / `test_error` | Zero signal in failure output. | `test_<unit>_<scenario>_<expected_behavior>`. |

---

## Interaction with other skills

- **[coding-guidance-python](../coding-guidance-python/SKILL.md)** owns the rules for *production* code under test — typing, `pathlib.Path` discipline, security, error handling, module shape. This skill does not restate those.
- **[git-conventions](../git-conventions/SKILL.md)** — bug-fix commits carry the Prove-It test in the same commit as the fix, and the commit body names the symptom.

---

## Common rationalizations

The thoughts that lead to the test getting skipped, and what's actually true.

| Rationalization | Reality |
|---|---|
| "I'll add tests after `make check` passes" | `make check` only proves lint, types, and *existing* tests are clean — it tells you nothing about new behavior. The test for new behavior is what makes `make check` meaningful. |
| "It's just a state machine helper, too simple to test" | State machines fail at the edges — re-entry, missing fields, stale slices. The bug example above is *exactly* this kind of "too simple" function. |
| "I tested it manually by running `/forge:execute`" | A manual lifecycle walk doesn't persist. Tomorrow's refactor has nothing to fall back on, and the next agent has no executable contract to read. |
| "Tests slow me down" | Tests slow you down now, and speed you up every subsequent change. The tradeoff is against *future you*, who is forgetful. |
| "The schema already validates it" | Schemas validate payload shape; they don't validate transitions, file IO, or error wrapping. Those are the tool's job, and the tool needs its own tests. |
| "Mocking's fine, I'll fix it later" | Once mocks proliferate, nothing removes them. FORGE code is mostly pure functions on `Path` — the real call is almost always cheaper than the mock. |
| "All tests pass" when you haven't run `make check` | `make check` is the floor. "Tests pass" without it is half-truth — lint and type errors block merge too. |

---

## Red flags

If you catch yourself doing any of these, stop and course-correct:

- Writing production code in `tools/` without a corresponding test in the same change.
- Writing a test that passes on its first run without having seen it fail.
- A bug-fix commit with no reproduction test.
- `unittest.mock.patch("tools.…")` reaching through the module system.
- A test reading from `docs/features/<a real feature>` instead of a `tmp_path` fixture.
- A test hardcoding `Path("schemas/…")` instead of using `schemas_dir`.
- Tests whose names don't describe the asserted behavior.
- "All tests pass" when you haven't actually run `make check`.
- A new test that re-implements a JSON Schema rule in Python instead of validating the schema itself.
- Tests skipped or `xfail`ed without an issue link or a deadline.

---

## Verification

A test change — or a feature change with tests — is done when:

- New or changed behavior has a test, and that test *failed* against the pre-change code.
- Tests live under `tests/tools/…` mirroring the `tools/` layout, or under `tests/smoke/` for end-to-end fixture walks.
- No `unittest.mock.patch` reaching through module paths.
- No async machinery anywhere — no async pytest plugin config, no `@pytest.mark.asyncio`, no `asyncio.timeout`.
- Filesystem work goes through `tmp_path`; schema and template lookups go through `schemas_dir` / `templates_dir`.
- Test names read as sentences — `test_<unit>_<scenario>_<expected_behavior>`.
- Bug-fix commit includes the reproduction test (see [git-conventions](../git-conventions/SKILL.md)).
- Every test function is fully type-annotated (`-> None`, typed parameters); fixtures have typed return types.
- `make check` passes clean — lint, typecheck, test all green. This is the floor.
- No new `# type: ignore` without a reason code and comment.
- No new dependency added without AGENTS.md "Ask first" clearance.

---

## Examples

**Good TDD slice shape:**

- *One* new test, one new or changed function in `tools/`, both in the same commit.
- Test asserts on observable state — the JSON the tool wrote, the dict it returned, the exception class and message it raised.
- `tmp_path` for filesystem; `schemas_dir` / `templates_dir` for schema and template lookups.
- Type annotations on the test function; no docstring needed.
- `make check` clean.
- Commit body explains *why* the new behavior is wanted, not *what* the code does.

**Good Prove-It shape:**

- Test written first, named after the bug's observable symptom (e.g. `test_start_phase_clears_current_slice_when_re_entering_phase`).
- Test fails against `HEAD~1` (the pre-fix tree), passes against the fix.
- Same commit contains test + fix. Commit body: symptom → test → fix → verification.
- Only one behavioral change in the commit; unrelated cleanup deferred.

**Bad shape that passes `make test` and is still wrong:**

```python
# tests/tools/test_state.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tools import state as state_module


def test_start_phase_works(tmp_path: Path) -> None:
    with patch("tools.state.write_state") as mock_write:           # (1) mocks the tool's own helper
        with patch("tools.state.Path") as mock_path:               # (2) mocks `pathlib.Path`
            state_module.start_phase(tmp_path, "spec")
    assert mock_write.called                                       # (3) interaction assertion
```

Three things are wrong at once:

1. `patch("tools.state.write_state")` reaches through the module system to mock the tool's own helper. The moment `write_state` is renamed or inlined, the test silently passes while actually testing nothing.
2. `patch("tools.state.Path")` mocks `pathlib.Path` — the very type the rest of the test depends on. There is no scenario in which this is the right move.
3. `assert mock_write.called` is an interaction assertion — green against a refactor that batches writes, green against a no-op, green against the wrong path. State-based (`json.loads((tmp_path / "STATE.json").read_text())["phase"] == "spec"`) would have caught all three.

This test is worse than no test: it occupies a file named after the right behavior, lulls reviewers into approving, and will silently pass through every refactor that matters. Delete it and rewrite using `tmp_path` and real I/O.

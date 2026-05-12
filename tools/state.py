"""Read, write, and transition feature state.json files."""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import tempfile
import threading
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import jsonschema

if sys.platform == "win32":  # pragma: no cover - platform-conditional
    import msvcrt

    _LOCK_BYTES: Final[int] = 0x7FFFFFFF
else:
    import fcntl

from tools._repo_root import discover_repo_root
from tools.validate._finding import Finding
from tools.validate.plan import validate_plan_tasks, validate_verified_deps
from tools.validate.spec_semantic import validate_anchors, validate_scenarios
from tools.validate.state_semantic import validate_deviations
from tools.validate.tdd_evidence import validate_tdd_evidence


class StateError(RuntimeError):
    """Raised when state.json cannot be read, parsed, or transitioned."""


class LockingNotSupportedError(StateError):
    """Raised when ``state_lock`` cannot acquire on the current platform.

    Defensive: covers exotic platforms (not POSIX, not Win32) where
    neither ``fcntl.flock`` nor ``msvcrt.locking`` is available. Linux,
    macOS, WSL, and native Windows are all supported and do not raise.
    """


class PhasePreconditionError(StateError):
    """Raised when ``start_phase`` is called without its lifecycle precondition.

    Two flavors:

    1. The prior phase in the feature's canonical phase list is not yet
       ``done``. Caller jumped ahead in the lifecycle.
    2. The target phase is already ``done``. Caller asked for a phase
       that has already finished.

    Both flavors are bypassable via ``start_phase(..., force=True)``, but
    callers wanting an audited bypass should use
    :func:`tools.recovery.recover_force_start_phase` so the decision is
    written into ``decisions.md`` instead of vanishing.
    """


# Thread-local set of state.json paths currently held by this process.
# ``state_lock`` is non-re-entrant: a nested acquisition against any path
# already in this set raises ``RuntimeError``. Per-thread storage avoids
# false re-entrancy collisions across genuinely independent worker threads.
_held_state_locks: Final[threading.local] = threading.local()


def _held_lock_paths() -> set[str]:
    """Return the per-thread set of currently-held state-lock paths."""
    holdings = getattr(_held_state_locks, "paths", None)
    if holdings is None:
        holdings = set()
        _held_state_locks.paths = holdings
    return holdings


def _acquire_exclusive(fd: int) -> None:
    """Acquire a cross-platform exclusive lock on ``fd``.

    POSIX path uses ``fcntl.flock(LOCK_EX)`` — true blocking.
    Win32 path uses ``msvcrt.locking(LK_LOCK, _LOCK_BYTES)`` which
    retries 10 times at 1-second intervals before raising ``OSError``;
    that ceiling matches typical state-mutation runtimes (well under a
    second) and surfaces a real deadlock instead of hanging forever.
    """
    if sys.platform == "win32":  # pragma: no cover - platform-conditional
        msvcrt.locking(fd, msvcrt.LK_LOCK, _LOCK_BYTES)
    elif sys.platform.startswith(("linux", "darwin", "freebsd", "openbsd", "netbsd")):
        fcntl.flock(fd, fcntl.LOCK_EX)
    else:  # pragma: no cover - exotic platforms
        raise LockingNotSupportedError(
            f"state_lock has no implementation for sys.platform={sys.platform!r}; "
            "supported: linux, darwin, win32, BSDs."
        )


def _release_exclusive(fd: int) -> None:
    """Release the lock acquired by :func:`_acquire_exclusive`."""
    if sys.platform == "win32":  # pragma: no cover - platform-conditional
        msvcrt.locking(fd, msvcrt.LK_UNLCK, _LOCK_BYTES)
    elif sys.platform.startswith(("linux", "darwin", "freebsd", "openbsd", "netbsd")):
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def state_lock(state_path: Path) -> Generator[None]:
    """Hold an exclusive advisory lock on ``<state_path>.lock`` for the body.

    Serializes read-modify-write helpers in this module so concurrent
    callers cannot overwrite each other's audit entries.

    Cross-platform: POSIX uses ``fcntl.flock(LOCK_EX)``; native Win32
    uses ``msvcrt.locking(LK_LOCK)``. The sidecar lockfile is opened in
    binary write mode so both syscalls accept the descriptor.

    Non-re-entrant: nested acquisition in the same thread raises
    ``RuntimeError``. Exotic platforms (not POSIX, not Win32) raise
    :class:`LockingNotSupportedError`.

    Args:
        state_path: state.json path being mutated. The sidecar lockfile
            is created at ``<state_path>.lock``.

    Yields:
        ``None`` once the lock is held.

    Raises:
        LockingNotSupportedError: platform is neither POSIX nor Win32.
        RuntimeError: nested acquisition in the same thread.
        OSError: lockfile cannot be created or the locking syscall
            failed (Win32 surfaces a deadlock after ~10 seconds).
    """
    key = str(state_path.resolve()) if state_path.exists() else str(state_path)
    holdings = _held_lock_paths()
    if key in holdings:
        raise RuntimeError(
            f"state_lock is non-re-entrant; already held for {key!r} "
            "in this thread. Acquire once at the outermost mutation site."
        )

    lock_path = state_path.with_name(state_path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = open(lock_path, "wb")  # noqa: SIM115, PTH123
    try:
        _acquire_exclusive(lock_fh.fileno())
        holdings.add(key)
        try:
            yield
        finally:
            holdings.discard(key)
            _release_exclusive(lock_fh.fileno())
    finally:
        lock_fh.close()


class _NoValidate:
    """Sentinel type: callers pass ``NO_VALIDATE`` to skip schema validation."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "NO_VALIDATE"


NO_VALIDATE: Final[_NoValidate] = _NoValidate()
"""Sentinel: pass to ``read_state``/``write_state`` to skip schema validation
entirely, even when a schema would be discoverable by autodiscovery."""


# Public type alias for the ``schema_path`` argument of ``read_state`` and
# ``write_state``. Only these two functions accept ``NO_VALIDATE``; all
# downstream helpers thread ``Path | None`` and let autodiscovery handle the
# ``None`` case transparently.
type _SchemaPathArg = Path | None | _NoValidate

# Hard cap on the directory walk in ``_autodiscover_state_schema``. Defensive:
# legitimate repo trees never need more than ~6 levels, and a runaway walk
# would touch the filesystem far more than the read/write helpers should.
_AUTODISCOVER_DEPTH_CAP: Final[int] = 12


# Strict feature-id: ``YYYY-MM-DD`` + alnum-leading slug with no trailing
# hyphen and no consecutive hyphens. Month is constrained to ``01-12`` and
# day to ``01-31`` so impossible calendar segments (e.g. ``2026-13-99-foo``)
# are rejected here AND at the schema boundary. The schema pattern at
# ``schemas/state.schema.json#properties.feature_id.pattern`` mirrors this
# regex string for byte; the runtime guard in
# ``tools.archive._FEATURE_ID_RE`` mirrors it too.
_FEATURE_ID_RE = re.compile(
    r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])-[a-z0-9](?:[a-z0-9]|-(?=[a-z0-9]))+$"
)

# Git commit SHA: 7-40 lowercase hex characters. Mirrors the schema pattern
# at ``schemas/state.schema.json#commits.items.properties.sha``.
_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


VALID_TIERS = ("focused", "standard", "full")

# Soft cap on refined_idea length. The refined idea is lifted verbatim into
# SPEC.md Intent and consumed by every downstream phase prompt; an unbounded
# blob is both a context-budget hazard and a leakage surface for any secrets
# or credentials a user pastes into the original idea text. We refuse rather
# than truncate so the caller has to make the trim decision deliberately.
_REFINED_IDEA_MAX_CHARS: Final[int] = 4000

# Hard cap on the Socratic refine loop. The forge-refine SKILL prose advertises
# a "max 5 rounds" cap; this constant is the machine-side enforcement that
# refuses to advance past it (deep-M-A1). Mirrored in the schema as
# ``routing.refine_attempts.maximum`` so a tampered state.json is rejected on
# read/write too.
_REFINE_ATTEMPTS_CAP: Final[int] = 5

# Current state-machine generation written by new features. v1 is the legacy
# 9-step standard tier; v2 collapsed standard to 5 steps; v3 adds the post-ship
# ``qa`` phase. ``flow_version`` is optional in state.json — absence is
# treated as v1 by application convention.
_FLOW_VERSION_V3: Final[int] = 3

# Schema resolved from the FORGE plugin install location, not from the
# caller-supplied ``repo_root``. The latter points at the user's target
# repository, which has no obligation to carry a copy of FORGE's schemas.
# Pattern mirrors ``tools/check_schemas.py`` and ``tools/cross_ai/config.py``.
_STATE_SCHEMA_PATH: Final[Path] = (
    Path(__file__).resolve().parents[1] / "schemas" / "state.schema.json"
)


# Canonical per-tier phase orderings, sourced from spec sections 6.1-6.3
# (`docs/specs/2026-05-09-m8-research-and-cross-ai-design.md`). The full-tier
# list carries the post-ship ``qa`` step only when the feature is at
# ``flow_version >= 3``; v1/v2 stop at ``ship`` per spec line 730. Standard
# tier intentionally omits ``research``: research is opt-in via
# ``/forge:do --research`` (which writes ``routing.phase_list`` explicitly),
# so the lazy default for legacy standard features is the no-research list.
_PHASE_LIST_FOCUSED: Final[tuple[str, ...]] = ("spec", "execute", "verify")
_PHASE_LIST_STANDARD: Final[tuple[str, ...]] = (
    "spec",
    "scenarios",
    "plan",
    "crucible",
    "review",
    "execute",
    "verify",
    "ship",
)
_PHASE_LIST_FULL_PRE_V3: Final[tuple[str, ...]] = (
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
)
_PHASE_LIST_FULL_V3: Final[tuple[str, ...]] = (*_PHASE_LIST_FULL_PRE_V3, "qa")


def derive_phase_list(*, tier: str, flow_version: int | None = None) -> list[str]:
    """Return the canonical lifecycle phase list for ``(tier, flow_version)``.

    Pure transformation. Does no I/O and reads no payload state. Callers
    typically reach this via ``get_phase_list`` rather than directly; the
    only direct caller is the schema-bounded routing seeder.

    Args:
        tier: One of ``VALID_TIERS``.
        flow_version: Optional state-machine generation. Absence is treated
            as v1 by application convention (matches ``read_state``); the
            full-tier list carries the trailing ``qa`` step only when this
            is ``>= _FLOW_VERSION_V3``.

    Returns:
        A fresh ``list[str]`` of lifecycle phase names in execution order.

    Raises:
        StateError: when ``tier`` is not in ``VALID_TIERS``.
    """
    if tier == "focused":
        return list(_PHASE_LIST_FOCUSED)
    if tier == "standard":
        return list(_PHASE_LIST_STANDARD)
    if tier == "full":
        if (flow_version or 1) >= _FLOW_VERSION_V3:
            return list(_PHASE_LIST_FULL_V3)
        return list(_PHASE_LIST_FULL_PRE_V3)
    raise StateError(f"unknown tier {tier!r}; must be one of {VALID_TIERS}")


def get_phase_list(payload: dict[str, Any]) -> list[str] | None:
    """Return the canonical phase list for ``payload``, or ``None``.

    Read-only accessor over an already-parsed ``state.json`` payload. Pure:
    never mutates ``payload`` and never touches the filesystem. Decision 4
    of the M8 P0 plan: the dict returned by ``read_state`` is **not**
    augmented with a derived ``phase_list``; callers that need the list
    reach for it through this accessor instead. That preserves the
    ``read → mutate → write_state`` round-trip guarantee for legacy
    features whose on-disk file lacks the field.

    Resolution order:

    1. ``payload['routing']`` is not a dict → ``None``.
    2. ``payload['routing']['phase_list']`` is a non-empty list → fresh
       ``list(...)`` copy of it (defensive against caller mutation).
    3. Otherwise look up ``tier = payload['tier']``; when ``tier`` is in
       ``VALID_TIERS``, derive the canonical list via
       :func:`derive_phase_list`. Unknown tier (or absent tier) → ``None``.

    Args:
        payload: Parsed ``state.json`` mapping.

    Returns:
        Ordered list of lifecycle phase names, or ``None`` when no list
        can be resolved.
    """
    routing = payload.get("routing")
    if not isinstance(routing, dict):
        return None
    explicit = routing.get("phase_list")
    if isinstance(explicit, list) and explicit:
        return list(explicit)
    tier = payload.get("tier")
    if isinstance(tier, str) and tier in VALID_TIERS:
        flow_version = payload.get("flow_version")
        fv = (
            flow_version
            if isinstance(flow_version, int) and not isinstance(flow_version, bool)
            else None
        )
        return derive_phase_list(tier=tier, flow_version=fv)
    return None


def _validator_for(schema: dict[str, Any]) -> jsonschema.Draft202012Validator:
    return jsonschema.Draft202012Validator(
        schema,
        format_checker=jsonschema.Draft202012Validator.FORMAT_CHECKER,
    )


def _autodiscover_state_schema(path: Path) -> Path | None:
    """Walk up from ``path.parent`` looking for ``schemas/state.schema.json``.

    Rules (kept deliberately explicit — this is the only filesystem-snooping
    helper in the module):

    * Resolve ``path`` and start the walk from its parent directory.
    * At each level, if ``<level>/schemas/state.schema.json`` exists, return it.
    * If the level contains a ``.forge/`` or ``.git/`` directory without a
      schema match at that level, stop and return ``None`` — the walk has
      reached a workspace boundary.
    * Stop at the filesystem root (``parent == self``) and return ``None``.
    * Hard cap the walk at :data:`_AUTODISCOVER_DEPTH_CAP` levels to bound
      filesystem activity; in practice no legitimate tree needs more than
      six.

    Distinct from :func:`tools._repo_root.discover_repo_root`: that helper
    answers "where is the FORGE repo root?" by treating ``.forge/`` as the
    target marker. This helper answers "where is the on-disk schema file?"
    by treating ``.forge/`` and ``.git/`` as stop boundaries. The two are
    kept separate because converging them would require one to grow knobs
    it does not need.

    Args:
        path: Target state.json path (existence not required).

    Returns:
        Absolute path to the discovered ``state.schema.json``, or ``None``
        when nothing matched within the cap.
    """
    try:
        current = path.resolve().parent
    except OSError:
        return None
    for _ in range(_AUTODISCOVER_DEPTH_CAP):
        candidate = current / "schemas" / "state.schema.json"
        if candidate.is_file():
            return candidate
        if (current / ".forge").is_dir() or (current / ".git").is_dir():
            return None
        parent = current.parent
        if parent == current:
            return None
        current = parent
    return None


def _resolve_schema_path(path: Path, schema_path: _SchemaPathArg) -> Path | None:
    """Return the effective schema path for ``path``, honoring the sentinel.

    * ``NO_VALIDATE`` → ``None`` (caller skips validation).
    * Explicit ``Path`` → that path (caller validates against it).
    * ``None`` → autodiscovery; may still return ``None`` when nothing matched.
    """
    if isinstance(schema_path, _NoValidate):
        return None
    if schema_path is None:
        return _autodiscover_state_schema(path)
    return schema_path


def read_state(path: Path, schema_path: _SchemaPathArg = None) -> dict[str, Any]:
    """Read, parse, and (optionally) schema-validate a state.json file.

    Format-aware: when a schema is in effect, the validator enforces
    ``format: date-time`` against RFC 3339 timestamps via the
    ``rfc3339-validator`` extra.

    Args:
        path: Path to the state.json file.
        schema_path: Three-mode schema selector.

            * ``None`` (default) — autodiscover ``schemas/state.schema.json``
              by walking up from ``path`` (see
              :func:`_autodiscover_state_schema`). When nothing is found,
              validation is skipped silently.
            * An explicit ``Path`` — validate against that schema.
            * :data:`NO_VALIDATE` — skip validation entirely, even when a
              schema would be discoverable.

    Returns:
        Parsed state.json payload.

    Raises:
        StateError: File missing, invalid JSON, or schema validation fails.
    """
    if not path.exists():
        raise StateError(f"state.json not found at {path}")
    try:
        raw_payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateError(f"state.json at {path} is invalid JSON: {exc}") from exc
    if not isinstance(raw_payload, dict):
        raise StateError(
            f"state.json at {path}: expected JSON object at top level, "
            f"got {type(raw_payload).__name__}"
        )
    payload: dict[str, Any] = raw_payload

    effective_schema = _resolve_schema_path(path, schema_path)
    if effective_schema is not None:
        schema = json.loads(effective_schema.read_text(encoding="utf-8"))
        validator = _validator_for(schema)
        errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
        if errors:
            messages = "; ".join(e.message for e in errors)
            raise StateError(f"state.json at {path} fails schema: {messages}")

    return payload


def _utc_now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


VALID_LIFECYCLE_PHASES = (
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
    "qa",
)


VALID_REVIEW_TARGETS = ("plan", "code")


def write_state(
    path: Path,
    payload: dict[str, Any],
    schema_path: _SchemaPathArg = None,
) -> None:
    """Validate (when a schema is in effect) and write payload pretty-printed.

    On schema failure, no file is written.

    Args:
        path: Destination file.
        payload: State payload.
        schema_path: Three-mode schema selector.

            * ``None`` (default) — autodiscover ``schemas/state.schema.json``
              by walking up from ``path`` (see
              :func:`_autodiscover_state_schema`). When nothing is found,
              validation is skipped silently and the file is written.
            * An explicit ``Path`` — validate against that schema.
            * :data:`NO_VALIDATE` — skip validation entirely, even when a
              schema would be discoverable.

    Raises:
        StateError: Validation failed; file not written.
    """
    effective_schema = _resolve_schema_path(path, schema_path)
    if effective_schema is not None:
        schema = json.loads(effective_schema.read_text(encoding="utf-8"))
        validator = _validator_for(schema)
        errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
        if errors:
            messages = "; ".join(e.message for e in errors)
            raise StateError(f"refusing to write: payload fails schema: {messages}")

    _atomic_write_json(path, payload)


# Severity sets blocked by each phase's exit gate. ``HIGH`` is included for
# every phase whose SKILL prose advertises HIGH+BLOCK refusal; the ``execute``
# phase's ``tdd_evidence`` check intentionally lists only ``BLOCK`` so a
# ``LOW``/``INFO`` advisory finding from that validator does not refuse
# phase exit (see ``skills/forge-execute/SKILL.md`` — only BLOCK blocks).
_BLOCK_AND_HIGH: Final[tuple[str, ...]] = ("BLOCK", "HIGH")
_BLOCK_ONLY: Final[tuple[str, ...]] = ("BLOCK",)


@dataclass(frozen=True, slots=True, kw_only=True)
class _GateCheck:
    """One artifact-validator pairing for a phase-exit gate.

    Attributes:
        name: Short label used in the ``StateError`` message so the
            operator sees which validator raised. Mirrors the
            ``Finding.target`` vocabulary of the underlying validator.
        artifact: Filename (relative to the feature folder, i.e.
            ``state_path.parent``) that must exist for this check to run.
            Use the empty string ``""`` for checks that operate on the
            feature folder itself (e.g. deviations, tdd_evidence). When a
            non-empty artifact filename is supplied and the file is
            absent, the check is a no-op — mirroring the spec-gate
            "missing artifact ⇒ skip" policy so fixture-driven plumbing
            tests remain green.
        requires_forge_layout: When True the check is skipped unless the
            feature folder lives under a discoverable ``.forge/features/``
            tree. Used by execute-phase checks whose validators expect
            the canonical FORGE layout (``validate_tdd_evidence`` resolves
            ``repo_root + feature_id``); fixture state.json files dropped
            in a bare ``tmp_path`` must not trip these gates because they
            are exercising state-machine plumbing, not feature semantics.
            ``validate_health`` is the right surface to flag a stray
            state.json in a live repo.
        blocking_severities: Severities that refuse phase exit when
            present in the check's findings.
        run: Callable that receives the feature folder and runs the
            validator, returning its findings list. The callable is the
            single seam responsible for binding the validator's actual
            signature (paths vs. ``(repo_root, feature_id)`` etc.) so
            ``_enforce_phase_gate`` can iterate uniformly.
    """

    name: str
    artifact: str
    blocking_severities: tuple[str, ...]
    run: Callable[[Path], list[Finding]]
    requires_forge_layout: bool = False


def _under_forge_features(feature_folder: Path) -> bool:
    """Return True when ``feature_folder`` sits under ``<repo>/.forge/features/``."""
    parent = feature_folder.parent
    grandparent = parent.parent
    return (
        parent.name == "features"
        and grandparent.name == ".forge"
        and discover_repo_root(feature_folder) is not None
    )


def _run_spec_scenarios(feature_folder: Path) -> list[Finding]:
    """Run ``validate_scenarios`` against ``SPEC.md`` next to state.json."""
    return validate_scenarios(feature_folder / "SPEC.md")


def _run_spec_anchors(feature_folder: Path) -> list[Finding]:
    """Run ``validate_anchors`` against ``SPEC.md`` next to state.json.

    Repo root is discovered via the shared ``.forge`` walk-up (mirroring the
    validator CLI's autodiscovery); the feature folder itself is the
    fall-back when no ``.forge/`` marker is found above the state path
    (e.g. unusual fixture trees).
    """
    spec_path = feature_folder / "SPEC.md"
    repo_root = discover_repo_root(spec_path) or feature_folder
    return validate_anchors(spec_path, repo_root=repo_root)


def _run_plan_tasks(feature_folder: Path) -> list[Finding]:
    """Run ``validate_plan_tasks`` with the paired SPEC.md next to PLAN.md."""
    return validate_plan_tasks(
        feature_folder / "PLAN.md",
        spec_path=feature_folder / "SPEC.md",
    )


def _run_plan_verified_deps(feature_folder: Path) -> list[Finding]:
    """Run ``validate_verified_deps`` offline (no live registry probe).

    ``check_registries=False`` mirrors the SKILL prose default so the
    mechanical gate does not introduce network I/O the SKILL itself
    leaves opt-in.
    """
    return validate_verified_deps(feature_folder / "PLAN.md", check_registries=False)


def _run_execute_deviations(feature_folder: Path) -> list[Finding]:
    """Run ``validate_deviations`` against the feature folder."""
    return validate_deviations(feature_folder)


def _run_execute_tdd_evidence(feature_folder: Path) -> list[Finding]:
    """Run ``validate_tdd_evidence`` resolving ``repo_root`` + ``feature_id``.

    Mirrors the CLI's ``(repo_root, feature_id)`` shape: ``feature_id`` is
    the folder name, ``repo_root`` is the ``.forge`` walk-up parent.
    """
    repo_root = discover_repo_root(feature_folder) or feature_folder.parent.parent.parent
    return validate_tdd_evidence(repo_root, feature_folder.name)


_PHASE_GATES: Final[Mapping[str, tuple[_GateCheck, ...]]] = {
    "spec": (
        _GateCheck(
            name="spec-semantic:scenarios",
            artifact="SPEC.md",
            blocking_severities=_BLOCK_AND_HIGH,
            run=_run_spec_scenarios,
        ),
        _GateCheck(
            name="spec-semantic:anchors",
            artifact="SPEC.md",
            blocking_severities=_BLOCK_AND_HIGH,
            run=_run_spec_anchors,
        ),
    ),
    "scenarios": (
        _GateCheck(
            name="scenarios",
            artifact="SPEC.md",
            blocking_severities=_BLOCK_AND_HIGH,
            run=_run_spec_scenarios,
        ),
    ),
    "plan": (
        _GateCheck(
            name="plan-tasks",
            artifact="PLAN.md",
            blocking_severities=_BLOCK_AND_HIGH,
            run=_run_plan_tasks,
        ),
        _GateCheck(
            name="verified-deps",
            artifact="PLAN.md",
            blocking_severities=_BLOCK_AND_HIGH,
            run=_run_plan_verified_deps,
        ),
    ),
    "execute": (
        _GateCheck(
            name="deviations",
            artifact="",
            blocking_severities=_BLOCK_AND_HIGH,
            run=_run_execute_deviations,
        ),
        _GateCheck(
            name="tdd_evidence",
            artifact="",
            blocking_severities=_BLOCK_ONLY,
            run=_run_execute_tdd_evidence,
            requires_forge_layout=True,
        ),
    ),
}


def _enforce_phase_gate(state_path: Path, phase: str) -> None:
    """Refuse phase exit when any registered validator returns blocking findings.

    Iterates the gate checks registered under ``phase`` in
    :data:`_PHASE_GATES`. For each check:

    * When ``artifact`` is non-empty and the file does not exist next to
      ``state.json``, the check is a no-op. Mirrors the original
      spec-gate "missing artifact ⇒ skip" policy so fixture-driven
      plumbing tests of the state machine remain green;
      ``validate_health`` is the right surface to flag a missing
      artifact in a live feature.
    * Otherwise the validator runs and its findings are filtered by
      ``blocking_severities``. Surviving findings are aggregated across
      every check for the phase so a single ``StateError`` lists the
      total count and every triggered check name. When two checks both
      fire (e.g. ``execute``'s ``deviations`` and ``tdd_evidence``) the
      error message surfaces both, not just the first.

    Phases without a registered gate are a no-op.

    Args:
        state_path: Path to the feature's ``state.json``.
        phase: The lifecycle phase being completed.

    Raises:
        StateError: When at least one check produced a blocking finding.
            The message names the phase, the total blocking-finding
            count, the triggered check names, and the feature folder.
    """
    gates = _PHASE_GATES.get(phase)
    if not gates:
        return
    feature_folder = state_path.parent
    blocking_total: list[Finding] = []
    triggered: list[str] = []
    artifacts: list[str] = []
    for check in gates:
        artifact_label = check.artifact or feature_folder.name
        if check.artifact and not (feature_folder / check.artifact).is_file():
            continue
        if check.requires_forge_layout and not _under_forge_features(feature_folder):
            continue
        findings = check.run(feature_folder)
        blocking = [f for f in findings if f.severity in check.blocking_severities]
        if blocking:
            triggered.append(check.name)
            blocking_total.extend(blocking)
            if artifact_label not in artifacts:
                artifacts.append(artifact_label)
    if not blocking_total:
        return
    severities = "/".join(_BLOCK_AND_HIGH)
    raise StateError(
        f"cannot complete phase {phase!r}: {len(blocking_total)} "
        f"{severities} finding(s) from {triggered} against "
        f"{artifacts} under {feature_folder}; resolve them and re-run"
    )


def complete_phase(
    path: Path,
    phase: str,
    schema_path: Path | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Mark `phase` done with completed_at timestamp. Persist and return new state.

    Lifecycle ordering is enforced: ``phase`` must be the current phase and
    its existing status must be ``in_progress``. ``current_phase`` is NOT
    changed; call ``start_phase`` next to move forward.

    Args:
        path: state.json path.
        phase: Lifecycle phase name to complete.
        schema_path: Optional schema for read+write validation.
        now: Optional ISO 8601 timestamp; defaults to UTC now.

    Returns:
        Updated state payload.

    Raises:
        StateError: Unknown phase, missing ``phases`` map, missing entry,
            phase is not the current phase, status is not ``in_progress``,
            or schema validation fails.
    """
    if phase not in VALID_LIFECYCLE_PHASES:
        raise StateError(f"unknown phase '{phase}'; must be one of {VALID_LIFECYCLE_PHASES}")

    with state_lock(path):
        payload = read_state(path, schema_path=schema_path)
        timestamp = now or _utc_now_iso()

        if "phases" not in payload or not isinstance(payload["phases"], dict):
            raise StateError("state.json is missing the required `phases` mapping")
        if phase not in payload["phases"]:
            raise StateError(f"cannot complete phase '{phase}': not present in phases")
        if payload.get("current_phase") != phase:
            raise StateError(
                f"cannot complete phase '{phase}': "
                f"current_phase is '{payload.get('current_phase')}'"
            )
        current_status = payload["phases"][phase].get("status")
        if current_status != "in_progress":
            raise StateError(
                f"cannot complete phase '{phase}': "
                f"status is '{current_status}', expected 'in_progress'"
            )

        if phase == "review":
            targets_done = sorted(payload["phases"][phase].get("targets_done", []))
            required = sorted(VALID_REVIEW_TARGETS)
            if targets_done != required:
                raise StateError(
                    f"cannot complete phase 'review': both review targets must be done; "
                    f"targets_done={targets_done}, required={required}"
                )

        _enforce_phase_gate(path, phase)

        payload["phases"][phase]["status"] = "done"
        payload["phases"][phase]["completed_at"] = timestamp

        # Anchor for post-merge /forge:qa: stamp top-level ``shipped_at`` exactly
        # once on ship completion. If a prior completion already wrote the field
        # (e.g. an artificial fixture or replay) preserve the original timestamp
        # so the post-merge guard reads a stable point-in-time value.
        if phase == "ship" and "shipped_at" not in payload:
            payload["shipped_at"] = timestamp

        write_state(path, payload, schema_path=schema_path)
        return payload


def _tier_allowed_phases(tier: str) -> frozenset[str]:
    """Return the set of phases legitimately reachable on the given tier.

    Computed from the per-tier next-phase tables (``_FOCUSED_NEXT``,
    ``_STANDARD_NEXT``, ``_FULL_NEXT``) plus the ``review`` phase (whose
    next-command lives in ``_next_review_command`` rather than the table)
    on tiers that flow through review, plus the post-ship ``qa`` phase
    introduced by ``flow_version: 3``. ``start_phase`` refuses a
    tier-incompatible phase (e.g. ``start_phase("refine")`` on a
    focused-tier feature) so the next-phase pump cannot end up on a
    dead-end ``None``.

    Unknown tier returns an empty set so the caller refuses defensively.
    """
    table_keys: set[str] = set()
    extras: set[str] = {"qa"}
    if tier == "focused":
        table_keys = set(_FOCUSED_NEXT.keys())
    elif tier == "standard":
        table_keys = set(_STANDARD_NEXT.keys())
        # Standard tier flows through review (crucible -> review --target plan,
        # execute -> review --target code); review's next-command resolves via
        # _next_review_command, so it isn't a key in _STANDARD_NEXT but is a
        # legitimately reachable phase on this tier.
        extras.add("review")
    elif tier == "full":
        table_keys = set(_FULL_NEXT.keys())
        extras.add("review")
    else:
        return frozenset()
    return frozenset(table_keys | extras)


def start_phase(
    path: Path,
    phase: str,
    schema_path: Path | None = None,
    now: str | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Set `current_phase = phase` and create/replace its phases entry as in_progress.

    Lifecycle precondition: ``phase`` must be the next legitimate slot
    in the feature's canonical phase list. Two refusals fire as
    :class:`PhasePreconditionError`:

    1. The prior phase in :func:`get_phase_list` is not yet ``done``.
    2. The target phase already exists with ``status == "done"``.

    Idempotent re-entry: if the target phase is already in ``payload[phases]``
    with ``status in {"pending", "in_progress"}``, the call succeeds and
    re-stamps ``started_at``. This matches the resume semantics that the
    SKILL prose advertises for review-target carry-over.

    Recovery: ``force=True`` bypasses both precondition checks. Pass
    ``force=True`` directly only from short-lived scripts; long-lived
    recovery should go through :func:`tools.recovery.recover_force_start_phase`
    which appends an audited ADR to ``decisions.md``.

    Args:
        path: state.json path.
        phase: Lifecycle phase name to start.
        schema_path: Optional schema for read+write validation.
        now: Optional ISO 8601 timestamp; defaults to UTC now.
        force: When ``True``, skip the precondition check. The caller is
            responsible for the audit trail; ``start_phase`` writes
            nothing to ``decisions.md``.

    Returns:
        Updated state payload.

    Raises:
        StateError: Unknown phase, phase not allowed on the seeded
            ``state.json.tier``, or schema failure.
        PhasePreconditionError: Prior phase is not done, or target phase
            already done, and ``force`` was not set.
    """
    if phase not in VALID_LIFECYCLE_PHASES:
        raise StateError(f"unknown phase '{phase}'; must be one of {VALID_LIFECYCLE_PHASES}")

    with state_lock(path):
        payload = read_state(path, schema_path=schema_path)
        timestamp = now or _utc_now_iso()

        # Cross-check phase against seeded tier so a focused/standard feature
        # cannot end up on a refine/domain slot (where the next-phase pump
        # returns None and the lifecycle gets stuck).
        tier = payload.get("tier")
        if isinstance(tier, str):
            allowed = _tier_allowed_phases(tier)
            if phase not in allowed:
                raise StateError(f"phase {phase!r} not allowed on tier {tier!r}")

        if "phases" not in payload or not isinstance(payload["phases"], dict):
            raise StateError("state.json is missing the required `phases` mapping")

        if not force:
            _enforce_phase_precondition(payload, phase)

        new_block: dict[str, Any] = {"status": "in_progress", "started_at": timestamp}
        if phase == "review":
            prior = payload["phases"].get("review")
            if isinstance(prior, dict):
                for carry in ("targets_done", "current_target"):
                    if carry in prior:
                        new_block[carry] = prior[carry]
        payload["phases"][phase] = new_block
        payload["current_phase"] = phase

        write_state(path, payload, schema_path=schema_path)
        return payload


def _enforce_phase_precondition(payload: dict[str, Any], phase: str) -> None:
    """Refuse to advance to ``phase`` when the lifecycle precondition is unmet.

    Two refusals raise :class:`PhasePreconditionError`:

    1. ``phases[phase].status == "done"``: caller asked for a phase that
       has already finished. Idempotent re-entry only covers
       ``pending`` / ``in_progress``.
    2. The phase immediately preceding ``phase`` in the canonical phase
       list (per :func:`get_phase_list`) is not yet ``done``. The first
       phase has no prior and always passes.

    Review-target pivot: the canonical list places ``execute`` right
    after ``review``, but the SKILL prose runs ``review --target plan``
    BEFORE execute and ``review --target code`` AFTER execute. The
    ``review`` phase status therefore stays ``in_progress`` across the
    execute slot. This helper accepts that pivot when the prior
    ``review`` block records ``targets_done`` containing ``"plan"``.

    Unknown phase lists (legacy state.json without ``routing`` or
    ``tier``) skip the prior-phase check defensively — the
    ``_tier_allowed_phases`` guard at the call site already refuses
    unknown tiers, so this branch only hits artificial fixtures.
    """
    existing = payload.get("phases", {}).get(phase)
    if isinstance(existing, dict) and existing.get("status") == "done":
        raise PhasePreconditionError(
            f"cannot start phase {phase!r}: already done. "
            "Pass force=True to override (use tools.recovery for an audited path)."
        )

    phase_list = get_phase_list(payload)
    if not phase_list or phase not in phase_list:
        return

    idx = phase_list.index(phase)
    if idx == 0:
        return

    # Walk back past any phase that the routing layer marked skipped
    # (e.g. ``research`` on a standard-tier feature that opted out). A
    # skipped phase has no ``phases[<name>]`` entry to inspect; treat it
    # as transparent so the precondition lands on the nearest active
    # prior slot.
    skipped_phases = _skipped_phase_set(payload)
    cursor = idx - 1
    while cursor >= 0 and phase_list[cursor] in skipped_phases:
        cursor -= 1
    if cursor < 0:
        return

    prior = phase_list[cursor]
    prior_block = payload.get("phases", {}).get(prior)

    # Review-target pivot: execute starts while review is still in_progress,
    # provided the plan-target pass already completed.
    if prior == "review" and phase == "execute" and isinstance(prior_block, dict):
        targets_done = prior_block.get("targets_done", [])
        if isinstance(targets_done, list) and "plan" in targets_done:
            return

    prior_status = prior_block.get("status") if isinstance(prior_block, dict) else None
    if prior_status != "done":
        raise PhasePreconditionError(
            f"cannot start phase {phase!r}: prior phase {prior!r} is "
            f"{prior_status!r}, expected 'done'. "
            "Pass force=True to override (use tools.recovery for an audited path)."
        )


def _skipped_phase_set(payload: dict[str, Any]) -> frozenset[str]:
    """Return the set of phase names marked skipped in ``payload``."""
    skipped = payload.get("skipped")
    if not isinstance(skipped, list):
        return frozenset()
    names: set[str] = set()
    for entry in skipped:
        if isinstance(entry, dict):
            name = entry.get("phase")
            if isinstance(name, str):
                names.add(name)
    return frozenset(names)


def finish_feature(
    path: Path,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Mark the feature finished by setting current_phase = 'done'.

    Does not add a 'done' entry under `phases` (schema's propertyNames forbids it).

    Args:
        path: state.json path.
        schema_path: Optional schema for read+write validation.

    Returns:
        Updated state payload.
    """
    with state_lock(path):
        payload = read_state(path, schema_path=schema_path)
        payload["current_phase"] = "done"
        write_state(path, payload, schema_path=schema_path)
        return payload


def record_routing_decision(
    path: Path,
    *,
    idea: str,
    final_tier: str,
    proposed_tier: str | None = None,
    rationale: str | None = None,
    constitution_present: bool = False,
    phase_list: list[str] | None = None,
    schema_path: Path | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Record a routing decision in state.json.routing. Idempotent overwrite.

    Args:
        path: state.json path.
        idea: User-supplied idea text.
        final_tier: Tier the user confirmed (focused/standard/full).
        proposed_tier: Tier the router proposed before user override.
        rationale: One-sentence reason from the router or user.
        constitution_present: True when .forge/CONSTITUTION.md was loaded at routing time.
        phase_list: Optional ordered list of unique lifecycle phase names. When
            given, persisted into ``routing.phase_list`` and consumed by
            ``next_phase_command`` for sequencing. When ``None`` (default), the
            field is omitted and consumers fall back to the per-tier static
            table via ``get_phase_list``'s lazy-derive branch.
        schema_path: Optional schema for read+write validation.
        now: Optional ISO 8601 timestamp; defaults to UTC now.

    Returns:
        Updated state payload.

    Raises:
        StateError: schema validation failure on read or write.
    """
    if final_tier not in VALID_TIERS:
        raise StateError(f"invalid final_tier {final_tier!r}; must be one of {VALID_TIERS}")
    if proposed_tier is not None and proposed_tier not in VALID_TIERS:
        raise StateError(f"invalid proposed_tier {proposed_tier!r}; must be one of {VALID_TIERS}")

    with state_lock(path):
        payload = read_state(path, schema_path=schema_path)
        # Cross-check: final_tier must match the seeded state.json.tier so a
        # focused/standard feature cannot quietly end up with routing.final_tier
        # set to "full" (which would corrupt downstream phase-pump tables and
        # next_phase_command resolution). seed_routed_feature already passes
        # final_tier == state.tier on the happy path, so this guard never trips
        # the canonical seeded route — only mismatched re-calls.
        state_tier = payload.get("tier")
        if final_tier != state_tier:
            raise StateError(f"final_tier {final_tier!r} mismatches state.json.tier {state_tier!r}")
        block: dict[str, Any] = {
            "idea": idea,
            "final_tier": final_tier,
            "decided_at": now or _utc_now_iso(),
            "constitution_present": constitution_present,
        }
        if proposed_tier is not None:
            block["proposed_tier"] = proposed_tier
        if rationale is not None:
            block["rationale"] = rationale
        if phase_list is not None:
            # Defensive copy so caller mutations after the call do not leak into
            # the persisted block. Schema enforces uniqueItems + enum membership.
            block["phase_list"] = list(phase_list)
        payload["routing"] = block
        write_state(path, payload, schema_path=schema_path)
        return payload


def record_refined_idea(
    path: Path,
    *,
    refined: str,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Persist the refined idea paragraph to state.json.refined_idea.

    Args:
        path: state.json path.
        refined: Single-paragraph refined idea text.
        schema_path: Optional schema for read+write validation.

    Returns:
        Updated state payload.

    Raises:
        StateError: empty input, ``current_phase`` is not ``refine``,
            ``refined`` exceeds the length cap, or schema validation fails.
    """
    if not refined.strip():
        raise StateError("refined_idea must be non-empty")
    if len(refined) > _REFINED_IDEA_MAX_CHARS:
        raise StateError(
            f"refined_idea exceeds {_REFINED_IDEA_MAX_CHARS}-char cap "
            f"(got {len(refined)} chars); trim before persistence"
        )

    with state_lock(path):
        payload = read_state(path, schema_path=schema_path)
        current_phase = payload.get("current_phase")
        if current_phase != "refine":
            raise StateError(
                f"cannot record refined_idea: current_phase is {current_phase!r}, expected 'refine'"
            )
        payload["refined_idea"] = refined
        write_state(path, payload, schema_path=schema_path)
        return payload


def guard_refine_entry(path: Path, schema_path: Path | None = None) -> dict[str, Any]:
    """Guard ``/forge:refine`` entry on tier + phase BEFORE any mutation.

    Reads ``state.json`` once and refuses if the feature is not actually on
    the refine entry slot:

      * ``current_phase != "refine"``  → ``StateError`` (wrong phase).
      * ``tier != "full"``             → ``StateError`` (wrong tier).

    Returns the parsed payload so the caller can continue without a second
    read. The two error wordings are deliberately distinct so the SKILL
    prose can quote them verbatim and the operator sees which precondition
    failed.

    Args:
        path: state.json path.
        schema_path: Optional schema for validation on read.

    Returns:
        Parsed state.json payload (already-validated when ``schema_path`` is given).

    Raises:
        StateError: ``current_phase != "refine"`` OR ``tier != "full"``.
    """
    payload = read_state(path, schema_path=schema_path)
    current_phase = payload.get("current_phase")
    if current_phase != "refine":
        raise StateError(
            f"cannot enter refine: current_phase is {current_phase!r}, expected 'refine'"
        )
    require_full_tier(payload, phase="refine")
    return payload


def require_full_tier(payload: dict[str, Any], *, phase: str) -> None:
    """Raise ``StateError`` when ``payload['tier'] != 'full'``.

    Shared tier guard for full-tier-only phases (``refine``, ``domain``).
    The error message is deliberately uniform so the SKILL.md prose for
    each phase can quote the helper's raise verbatim instead of inventing
    a per-skill string (deep-M-A2 / deep-M-A6).

    Args:
        payload: Parsed state.json payload.
        phase: Phase name to embed in the error message (e.g. ``"refine"``).

    Raises:
        StateError: when ``payload`` does not carry ``tier == "full"``.
    """
    tier = payload.get("tier")
    if tier != "full":
        raise StateError(f"{phase} phase is full-tier only; current tier is {tier!r}")


def increment_refine_attempts(
    path: Path,
    schema_path: Path | None = None,
) -> int:
    """Increment ``routing.refine_attempts`` by 1; persist and return the new count.

    The routing block must already exist (seeded by ``/forge:do`` via
    ``record_routing_decision``). When ``refine_attempts`` is missing from the
    routing block, it is treated as 0 and seeded to 1 on the first call.
    Sibling routing fields are preserved.

    Args:
        path: state.json path.
        schema_path: Optional schema for read+write validation.

    Returns:
        The new ``refine_attempts`` count after increment.

    Raises:
        StateError: ``current_phase`` is not ``refine``, ``tier`` is not
            ``full``, the routing block is absent (call ``/forge:do`` first),
            ``routing.refine_attempts`` is present but not a non-negative
            integer, the count already sits at the ``_REFINE_ATTEMPTS_CAP``
            cap, or schema validation fails.
    """
    with state_lock(path):
        payload = read_state(path, schema_path=schema_path)

        current_phase = payload.get("current_phase")
        if current_phase != "refine":
            raise StateError(
                f"cannot increment refine_attempts: current_phase is "
                f"{current_phase!r}, expected 'refine'"
            )

        require_full_tier(payload, phase="refine")

        routing = payload.get("routing")
        if not isinstance(routing, dict):
            raise StateError(
                "cannot increment refine_attempts: routing block missing — "
                "/forge:do must run before /forge:refine"
            )

        raw_current: Any = routing.get("refine_attempts", 0)
        if not isinstance(raw_current, int) or isinstance(raw_current, bool):
            raise StateError(
                f"cannot increment refine_attempts: routing.refine_attempts "
                f"must be int, got {type(raw_current).__name__} "
                f"({raw_current!r}) in {path}"
            )
        current: int = raw_current
        if current < 0:
            raise StateError(
                f"cannot increment refine_attempts: routing.refine_attempts "
                f"is negative ({current}) in {path}"
            )
        if current >= _REFINE_ATTEMPTS_CAP:
            raise StateError(
                f"refine_attempts already at cap ({_REFINE_ATTEMPTS_CAP}); "
                f"record_refined_idea + complete_phase or surface a deviation"
            )
        new_count = current + 1
        routing["refine_attempts"] = new_count

        write_state(path, payload, schema_path=schema_path)
        return new_count


def set_review_target(
    path: Path,
    *,
    review_target: str,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Set phases.review.current_target and ensure targets_done is initialized.

    Args:
        path: state.json path.
        review_target: One of VALID_REVIEW_TARGETS.
        schema_path: Optional schema for read+write validation.

    Returns:
        Updated state payload.

    Raises:
        StateError: invalid review_target, missing review entry, or schema failure.
    """
    if review_target not in VALID_REVIEW_TARGETS:
        raise StateError(
            f"invalid review_target {review_target!r}; must be one of {VALID_REVIEW_TARGETS}"
        )
    with state_lock(path):
        payload = read_state(path, schema_path=schema_path)
        review_block = payload.get("phases", {}).get("review")
        if review_block is None:
            raise StateError("cannot set review_target: phases.review entry missing")
        review_status = review_block.get("status")
        if review_status != "in_progress":
            raise StateError(
                f"cannot set review_target: review status is "
                f"{review_status!r}, expected 'in_progress'"
            )
        review_block["current_target"] = review_target
        review_block.setdefault("targets_done", [])
        write_state(path, payload, schema_path=schema_path)
        return payload


def complete_review_target(
    path: Path,
    *,
    review_target: str,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Append `review_target` to phases.review.targets_done. Idempotent.

    Args:
        path: state.json path.
        review_target: Must equal phases.review.current_target.
        schema_path: Optional schema for read+write validation.

    Returns:
        Updated state payload.

    Raises:
        StateError: invalid target, missing review entry, mismatched current_target,
            or schema failure.
    """
    if review_target not in VALID_REVIEW_TARGETS:
        raise StateError(
            f"invalid review_target {review_target!r}; must be one of {VALID_REVIEW_TARGETS}"
        )
    with state_lock(path):
        payload = read_state(path, schema_path=schema_path)
        review_block = payload.get("phases", {}).get("review")
        if review_block is None:
            raise StateError("cannot complete review_target: phases.review entry missing")
        review_status = review_block.get("status")
        if review_status != "in_progress":
            raise StateError(
                f"cannot complete review_target: review status is {review_status!r}, "
                f"expected 'in_progress'"
            )
        current = review_block.get("current_target")
        if current != review_target:
            raise StateError(
                f"cannot complete review_target {review_target!r}: current_target is {current!r}"
            )
        targets_done = review_block.setdefault("targets_done", [])
        if review_target not in targets_done:
            targets_done.append(review_target)
        write_state(path, payload, schema_path=schema_path)
        return payload


def set_execute_current_slice(
    path: Path,
    *,
    slice_number: int,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Set ``phases.execute.current_slice`` and persist.

    Standard / full tier execute walks slice-by-slice; the orchestrator
    stamps the slice number before dispatching each wave so a mid-phase
    interruption can resume at the right cursor. Schema permits the field
    on every phase entry but ``forge-execute`` is the only consumer today,
    hence the execute-specific guards in this helper.

    Args:
        path: state.json path.
        slice_number: 1-based slice ordinal; schema minimum is 1.
        schema_path: Optional schema for read+write validation.

    Returns:
        Updated state payload.

    Raises:
        StateError: ``slice_number`` is not a positive int (bools are
            rejected even though they are an int subclass),
            ``current_phase`` is not ``"execute"``, the ``phases.execute``
            entry is missing or its status is not ``"in_progress"``, or
            schema validation fails.
    """
    if not isinstance(slice_number, int) or isinstance(slice_number, bool) or slice_number < 1:
        raise StateError(
            f"slice_number must be a positive int (got {type(slice_number).__name__} "
            f"{slice_number!r})"
        )
    with state_lock(path):
        payload = read_state(path, schema_path=schema_path)
        current_phase = payload.get("current_phase")
        if current_phase != "execute":
            raise StateError(
                f"cannot set execute.current_slice: current_phase is "
                f"{current_phase!r}, expected 'execute'"
            )
        phases = payload.get("phases")
        if not isinstance(phases, dict) or "execute" not in phases:
            raise StateError("cannot set execute.current_slice: phases.execute entry missing")
        execute_block = phases["execute"]
        if not isinstance(execute_block, dict):
            raise StateError(
                "cannot set execute.current_slice: phases.execute entry is not a mapping"
            )
        status = execute_block.get("status")
        if status != "in_progress":
            raise StateError(
                f"cannot set execute.current_slice: phases.execute.status is "
                f"{status!r}, expected 'in_progress'"
            )
        execute_block["current_slice"] = slice_number
        write_state(path, payload, schema_path=schema_path)
        return payload


def record_commit(
    path: Path,
    *,
    sha: str,
    phase: str,
    subject: str,
    logged_at: str | None = None,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Append a commit entry to ``state.commits[]``.

    Replaces ad-hoc Edit / Write calls against the live ``state.json`` —
    the state-writer hook refuses those, and bypassing it through Bash
    skips schema validation. The mutation is serialized via
    :func:`state_lock` (advisory ``fcntl.LOCK_EX`` on a sidecar lockfile)
    so concurrent callers cannot overwrite each other's audit entries,
    and the resulting payload is persisted atomically via
    :func:`_atomic_write_json` (tempfile + ``fsync`` + ``os.replace`` +
    parent-directory ``fsync``) so a crash mid-write cannot leave a torn
    file behind.

    Args:
        path: state.json path.
        sha: 7-40 lowercase hex character git SHA (matches schema pattern).
        phase: Lifecycle phase the commit belongs to.
        subject: Commit subject line; must be non-empty.
        logged_at: Optional RFC 3339 timestamp; defaults to UTC now.
        schema_path: Optional schema for read+write validation.

    Returns:
        Updated state payload.

    Raises:
        StateError: ``sha`` does not match the schema pattern, ``phase``
            is not in :data:`VALID_LIFECYCLE_PHASES`, ``subject`` is empty
            or not a string, ``state.commits`` is not a list, or schema
            validation fails.
        LockingNotSupportedError: caller is on native Win32.
    """
    if not isinstance(sha, str) or not _COMMIT_SHA_RE.fullmatch(sha):
        raise StateError(f"invalid commit sha {sha!r}; must be 7-40 lowercase hex chars")
    if phase not in VALID_LIFECYCLE_PHASES:
        raise StateError(f"invalid commit phase {phase!r}; must be one of {VALID_LIFECYCLE_PHASES}")
    if not isinstance(subject, str) or not subject:
        raise StateError("commit subject must be a non-empty string")

    with state_lock(path):
        payload = read_state(path, schema_path=schema_path)
        commits = payload.setdefault("commits", [])
        if not isinstance(commits, list):
            raise StateError("state.json `commits` field is not a list")
        entry: dict[str, Any] = {
            "sha": sha,
            "phase": phase,
            "subject": subject,
            "logged_at": logged_at or _utc_now_iso(),
        }
        commits.append(entry)
        write_state(path, payload, schema_path=schema_path)
        return payload


def append_deviation(
    path: Path,
    *,
    phase: str,
    cause: str,
    resolution: str,
    logged_at: str | None = None,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Append a deviation entry to ``state.deviations[]``.

    Replaces ad-hoc Edit / Write calls against the live ``state.json`` —
    the state-writer hook refuses those, and bypassing it through Bash
    skips schema validation. Use this whenever a phase logs a non-fatal
    deviation that ``tools.validate --target deviations`` will cross-check
    against ``decisions.md``.

    Args:
        path: state.json path.
        phase: Lifecycle phase the deviation belongs to.
        cause: Why the deviation was needed; non-empty string.
        resolution: What was done to address it; non-empty string.
        logged_at: Optional RFC 3339 timestamp; defaults to UTC now.
        schema_path: Optional schema for read+write validation.

    Returns:
        Updated state payload.

    Raises:
        StateError: ``phase`` is not in :data:`VALID_LIFECYCLE_PHASES`,
            ``cause`` or ``resolution`` is empty or not a string,
            ``state.deviations`` is not a list, or schema validation fails.
    """
    if phase not in VALID_LIFECYCLE_PHASES:
        raise StateError(
            f"invalid deviation phase {phase!r}; must be one of {VALID_LIFECYCLE_PHASES}"
        )
    if not isinstance(cause, str) or not cause:
        raise StateError("deviation cause must be a non-empty string")
    if not isinstance(resolution, str) or not resolution:
        raise StateError("deviation resolution must be a non-empty string")

    with state_lock(path):
        payload = read_state(path, schema_path=schema_path)
        deviations = payload.setdefault("deviations", [])
        if not isinstance(deviations, list):
            raise StateError("state.json `deviations` field is not a list")
        entry: dict[str, Any] = {
            "phase": phase,
            "cause": cause,
            "resolution": resolution,
            "logged_at": logged_at or _utc_now_iso(),
        }
        deviations.append(entry)
        write_state(path, payload, schema_path=schema_path)
        return payload


def feature_folder_exists(repo_root: Path | str, feature_id: str) -> bool:
    """Return True when .forge/features/<feature_id>/ exists under repo_root.

    Args:
        repo_root: Repository root containing the ``.forge/`` tree. A ``str``
            is accepted at the entry boundary and coerced to ``Path`` so
            agent callers that improvise on the call shape do not trip a
            cryptic ``TypeError`` at the first ``/`` operator.
        feature_id: Feature folder name under ``.forge/features/``.
    """
    return (Path(repo_root) / ".forge" / "features" / feature_id).is_dir()


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write ``payload`` to ``path`` durably and atomically.

    Implementation:

      1. Open a tempfile in the same directory as ``path`` so ``os.replace``
         is a same-filesystem rename (atomic on POSIX).
      2. Write the serialized payload, flush, and ``os.fsync`` the file
         descriptor so the bytes hit the platter / SSD cell.
      3. ``os.replace`` the tempfile over ``path`` (atomic rename).
      4. ``os.fsync`` the parent directory so the rename itself is durable
         across a power loss.

    On any failure between steps 1 and 4, the partial tempfile is unlinked
    before the original exception re-raises. The caller observes either the
    pre-call contents of ``path`` or the new payload — never a torn file.
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".state-", suffix=".json", dir=str(parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, indent=2, sort_keys=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        tmp_path.replace(path)
        _fsync_directory(parent)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp_path.unlink()
        raise


def _fsync_directory(directory: Path) -> None:
    """``fsync`` a directory inode so a preceding rename is durable.

    Best-effort: platforms that refuse to open a directory for fsync
    (rare; Windows is the documented case) swallow the error rather than
    fail the surrounding write.
    """
    try:
        dir_fd = os.open(str(directory), os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def migrate_to_v3(
    repo_root: Path | str,
    feature_id: str,
    schema_path: Path | None = None,
) -> dict[str, Any]:
    """Bump a feature's state.json to ``flow_version: 3`` and add a pending qa phase.

    The migration is gated on ship completion: a feature is considered shipped
    when ``state.shipped_at`` is set OR ``phases.ship.status == "done"``.
    Pre-ship features raise ``StateError`` so qa cannot run before the
    feature has actually been merged.

    The function is idempotent: calling it on a state that is already at
    ``flow_version: 3`` is a no-op (no disk write, payload returned as-is).
    Otherwise the bumped payload is persisted atomically via tempfile +
    ``os.replace`` so a crash mid-write cannot corrupt state.json.

    Args:
        repo_root: Repository root containing the ``.forge/`` tree. A ``str``
            is accepted at the entry boundary and coerced to ``Path`` so
            agent callers that improvise on the call shape do not trip a
            cryptic ``TypeError`` at the first ``/`` operator.
        feature_id: Feature folder name under ``.forge/features/``.
        schema_path: Optional schema path; defaults to the FORGE plugin
            install schema (``_STATE_SCHEMA_PATH``) so the helper works
            against any target repository.

    Returns:
        The (possibly updated) state.json payload.

    Raises:
        StateError: state.json is missing/invalid, the feature has not
            shipped yet, or schema validation fails after the bump.
    """
    if schema_path is None:
        schema_path = _STATE_SCHEMA_PATH
    state_path = Path(repo_root) / ".forge" / "features" / feature_id / "state.json"

    with state_lock(state_path):
        payload = read_state(state_path, schema_path=schema_path)

        if payload.get("flow_version") == _FLOW_VERSION_V3:
            return payload

        shipped_at = payload.get("shipped_at")
        ship_block = payload.get("phases", {}).get("ship", {})
        ship_done = isinstance(ship_block, dict) and ship_block.get("status") == "done"
        if not shipped_at and not ship_done:
            raise StateError(
                f"cannot migrate to v3 before ship completes: "
                f"feature {feature_id!r} has neither shipped_at nor phases.ship.status=='done'"
            )

        payload["flow_version"] = _FLOW_VERSION_V3
        phases = payload.setdefault("phases", {})
        if "qa" not in phases:
            phases["qa"] = {"status": "pending"}

        # Validate post-mutation against the schema before touching disk.
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        validator = _validator_for(schema)
        errors = sorted(validator.iter_errors(payload), key=lambda e: list(e.path))
        if errors:
            messages = "; ".join(e.message for e in errors)
            raise StateError(f"refusing to migrate: payload fails schema: {messages}")

        _atomic_write_json(state_path, payload)
        return payload


def find_active_feature(
    repo_root: Path | str,
    feature_id: str | None = None,
) -> Path:
    """Resolve which .forge/features/<id>/ to act on. Read-only.

    Precedence (D-S6 in M3 spec):
        1. Explicit `feature_id` arg wins.
        2. Else single active feature (state.json.current_phase != 'done').
        3. Else: zero active -> StateError; multiple active -> StateError listing them.

    Excludes any folder under `.forge/features/archive/`.

    Args:
        repo_root: Repository root containing the .forge/ tree. A ``str`` is
            accepted at the entry boundary and coerced to ``Path`` so agent
            callers that improvise on the call shape do not trip a cryptic
            ``TypeError`` at the first ``/`` operator.
        feature_id: Optional explicit feature id (matches folder name).

    Returns:
        Path to the resolved feature folder.

    Raises:
        StateError: when no feature matches, multiple active without explicit id,
            or the explicit id has no matching folder/state.json.
    """
    repo_root = Path(repo_root)
    features_root = repo_root / ".forge" / "features"
    if feature_id is not None:
        if not _FEATURE_ID_RE.fullmatch(feature_id):
            raise StateError(f"invalid feature id: {feature_id!r}")
        candidate = features_root / feature_id
        if not candidate.is_dir() or not (candidate / "state.json").exists():
            raise StateError(f"feature {feature_id!r} not found at {candidate}")
        return candidate

    if not features_root.is_dir():
        raise StateError("no active feature: .forge/features/ does not exist")

    active: list[Path] = []
    for entry in sorted(features_root.iterdir()):
        if not entry.is_dir() or entry.name == "archive":
            continue
        state_path = entry / "state.json"
        if not state_path.exists():
            continue
        try:
            payload = read_state(state_path)
        except StateError as exc:
            raise StateError(
                f"cannot resolve active feature: state.json under {entry.name} is invalid: {exc}"
            ) from exc
        if payload.get("current_phase") != "done":
            active.append(entry)

    if not active:
        raise StateError("no active feature: every feature is at current_phase='done'")
    if len(active) > 1:
        ids = ", ".join(p.name for p in active)
        raise StateError(f"multiple active features ({ids}); pass --feature <id> to disambiguate")
    return active[0]


_FOCUSED_NEXT: dict[str, str | None] = {
    "spec": "/forge:execute",
    "execute": "/forge:verify",
    "verify": None,
}

_STANDARD_NEXT: dict[str, str | None] = {
    # 'refine' is intentionally absent — refine is full-tier only and was
    # never supposed to enter the standard pipeline (deep-M-A2). Standard
    # tier starts at /forge:spec; the optional research opt-in (seeded via
    # routing.phase_list at /forge:do --standard --research time) lands the
    # feature at current_phase="research" and uses the entry below.
    "research": "/forge:spec",
    "spec": "/forge:scenarios",
    "scenarios": "/forge:plan",
    "plan": "/forge:crucible",
    "crucible": "/forge:review --target plan",
    "execute": "/forge:review --target code",
    "verify": "/forge:ship",
    "ship": "/forge:qa --against merged",
    "qa": None,
}

_FULL_NEXT: dict[str, str | None] = {
    **_STANDARD_NEXT,
    "refine": "/forge:research",
    "research": "/forge:spec",
    "spec": "/forge:domain",
    "domain": "/forge:scenarios",
}


def _next_review_command(state_payload: dict[str, Any]) -> str:
    """Resolve the next command when the current phase is `review`."""
    review = state_payload.get("phases", {}).get("review", {})
    done = review.get("targets_done", [])
    if "plan" not in done:
        return "/forge:review --target plan"
    if "code" not in done:
        return "/forge:execute"
    return "/forge:verify"


# Sentinel returned by ``_next_from_phase_list`` when ``routing.phase_list``
# applies and resolves to "terminal" (current_phase is the last entry and
# the static table has nothing to add). The caller surfaces this as
# ``None``; the bare ``None`` return means "phase_list did not apply, fall
# through to the static table."
_PHASE_LIST_TERMINAL: Final[object] = object()


def _next_from_phase_list(  # noqa: PLR0911
    state_payload: dict[str, Any],
) -> str | object | None:
    """Return the next slash-command when ``routing.phase_list`` applies.

    Resolution:

    * ``routing.phase_list`` absent / empty → ``None`` (caller falls back
      to the per-tier static table).
    * ``current_phase`` not in ``phase_list`` → ``None`` (legacy /
      inconsistent state; caller falls back to the static table to
      preserve backward compatibility).
    * ``current_phase == "execute"`` AND the list also contains
      ``review`` AND ``review.targets_done`` is missing ``code``: route
      back to ``/forge:review --target code`` to preserve the dual-pass
      review semantics. The phase_list is a single linear sequence and
      cannot encode the implicit second review visit; this re-routing
      mirrors the behavior the per-tier static table previously
      provided.
    * ``current_phase`` is the last entry → :data:`_PHASE_LIST_TERMINAL`
      sentinel (caller surfaces ``None`` from ``next_phase_command``).
    * Otherwise the next entry in ``phase_list`` drives the slash literal:
      ``review`` delegates to :func:`_next_review_command` (preserves the
      ``targets_done`` two-pass semantics); a ``ship → qa`` transition
      keeps the ``--against merged`` flag from the static table; every
      other phase becomes ``f"/forge:{next_phase}"``.

    PLR0911 silenced: each early-return is an independent guard against a
    distinct shape failure (missing routing / empty list / unknown phase /
    terminal / non-string entry); collapsing them obscures intent.

    Returns:
        Slash command ``str``, the :data:`_PHASE_LIST_TERMINAL` sentinel,
        or ``None``.
    """
    routing = state_payload.get("routing")
    if not isinstance(routing, dict):
        return None
    phase_list = routing.get("phase_list")
    if not isinstance(phase_list, list) or not phase_list:
        return None

    phase = state_payload.get("current_phase")
    if not isinstance(phase, str) or phase not in phase_list:
        return None

    # Dual-pass review semantics: phase_list is a single linear sequence
    # but the review phase is visited twice (target=plan then target=code).
    # Delegate to ``_next_review_command`` whenever the current phase is
    # review, and re-route execute → review/code when the code-target is
    # still pending. Both branches mirror the legacy static-table logic
    # so a phase_list-bearing feature behaves identically to its
    # legacy-fallback counterpart through the review/execute ping-pong.
    if phase == "review":
        return _next_review_command(state_payload)
    if phase == "execute" and "review" in phase_list:
        review_block = state_payload.get("phases", {}).get("review", {})
        targets_done = review_block.get("targets_done", []) or []
        if "code" not in targets_done:
            return "/forge:review --target code"

    idx = phase_list.index(phase)
    if idx == len(phase_list) - 1:
        # End of the explicit list. The seeder writes the v1/v2 list (no
        # ``qa``) for full-tier features, but the static table still
        # carries the ``ship → /forge:qa --against merged`` transition for
        # post-merge migration. Falling through to the static table only
        # when phase_list is exhausted preserves that bridge without
        # letting the static table override an in-flight phase_list walk.
        return _PHASE_LIST_TERMINAL

    next_phase = phase_list[idx + 1]
    if not isinstance(next_phase, str):
        return None

    if next_phase == "review":
        return _next_review_command(state_payload)
    if next_phase == "qa" and phase == "ship":
        return "/forge:qa --against merged"
    return f"/forge:{next_phase}"


def current_phase_command(state_payload: dict[str, Any]) -> str | None:
    """Return the slash-command for ``state_payload['current_phase']``, or None.

    Read-only. Pure function over a state.json payload. Returns the slash
    literal that runs the phase the feature is **currently in** — i.e., the
    command a user should invoke next when ``start_phase`` has already moved
    ``current_phase`` to a freshly-opened phase.

    This is the complement of :func:`next_phase_command`, which returns the
    slash for the phase **after** ``current_phase``. Use this helper after a
    ``complete_phase(prev) + start_phase(next)`` pair, when the prose needs to
    point the operator at the phase the lifecycle just transitioned **into**.
    Calling ``next_phase_command`` in that spot returns the phase after that,
    so the operator would be told to skip the just-opened phase.
    """
    phase = state_payload.get("current_phase")
    if not isinstance(phase, str):
        return None
    if phase == "done" or phase not in VALID_LIFECYCLE_PHASES:
        return None
    if phase == "review":
        return _next_review_command(state_payload)
    if phase == "qa":
        return "/forge:qa --against merged"
    return f"/forge:{phase}"


def next_phase_command(state_payload: dict[str, Any]) -> str | None:
    """Return the slash-command for the next pipeline phase, or None when done.

    Read-only. Pure function over a state.json payload.

    Resolution order:

    1. When ``routing.phase_list`` is present, non-empty, AND
       ``current_phase`` appears in it, the list drives the next
       command. End-of-list → ``None``.
    2. Otherwise fall back to the per-tier static table
       (``_FOCUSED_NEXT`` / ``_STANDARD_NEXT`` / ``_FULL_NEXT``).

    The fallback preserves backward compatibility for legacy features
    whose state.json predates the routing-block wire-up; the
    list-first preference honors any caller (e.g. the routing seeder)
    that wrote an explicit ordering.

    Args:
        state_payload: Parsed state.json (must contain `tier`, `current_phase`,
            and `phases`).

    Returns:
        Slash command string (e.g. '/forge:scenarios') or None when at terminal phase.
    """
    tier = state_payload.get("tier")
    phase = state_payload.get("current_phase")

    if not isinstance(phase, str) or not isinstance(tier, str):
        return None

    list_result = _next_from_phase_list(state_payload)
    if isinstance(list_result, str):
        return list_result
    if list_result is _PHASE_LIST_TERMINAL:
        # End of the explicit list. The seeder writes the v1/v2 phase list
        # without ``qa``, but the static full/standard table carries the
        # ``ship → /forge:qa --against merged`` post-merge bridge; honor
        # that transition so flow_version v3 features still land in qa
        # after ship completes. Every other terminal-of-list state returns
        # ``None`` per the routing-precedence contract.
        ship_to_qa = phase == "ship" and tier in ("standard", "full")
        return "/forge:qa --against merged" if ship_to_qa else None

    if tier == "focused":
        return _FOCUSED_NEXT.get(phase)

    table = {"standard": _STANDARD_NEXT, "full": _FULL_NEXT}.get(tier)
    if table is None:
        return None
    return _next_review_command(state_payload) if phase == "review" else table.get(phase)

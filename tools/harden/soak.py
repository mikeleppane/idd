"""Soak module for `/forge:harden`.

Long-running soak check that detects whether the feature exposes a
daemon/server entrypoint (``pyproject.toml`` ``[project.scripts]`` or
``package.json`` ``"bin"``). Libraries — projects with neither
declaration — return ``status="skipped"`` silently so the harden record
never claims confidence the soak did not earn.

When an entrypoint is detected, the module dispatches to an injected
``runner`` callable which is responsible for actually launching the
process, collecting RSS/CPU samples, and returning them. Subprocess
spawning lives in the harden orchestrator skill — this layer stays
pure-Python so unit tests can exercise the metric/aggregation logic
without spinning real processes.

Reuses :class:`HardenError` from :mod:`tools.harden.contract` so harden
modules surface a single error type.
"""

from __future__ import annotations

import json
import tomllib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

from tools.harden.contract import HardenError
from tools.validate._frontmatter import _read_text

DEFAULT_SOAK_MINUTES: Final[int] = 5
DEFAULT_MEMORY_GROWTH_THRESHOLD: Final[float] = 0.10

SoakStatus = Literal["pass", "fail", "skipped"]
EntrypointKind = Literal["python_script", "node_bin", "none"]

_DEFAULT_RUNNER_DETAIL: Final[str] = "no soak runner configured"
_NO_ENTRYPOINT_DETAIL: Final[str] = "no long-running entrypoint declared"

# Growth comparison needs at least a first and last sample to compute a ratio;
# anything shorter is treated as too-noisy-to-flag.
_MIN_SAMPLES_FOR_GROWTH_CHECK: Final[int] = 2


@dataclass(frozen=True)
class EntrypointInfo:
    """Detected long-running entrypoint declaration.

    Attributes:
        kind: ``python_script`` when a ``pyproject.toml``
            ``[project.scripts]`` table is present; ``node_bin`` when a
            ``package.json`` ``"bin"`` key is present; ``none`` when
            neither file declares one (libraries).
        name: Script/bin name (first key wins for object forms; the
            package name is used when ``"bin"`` is a bare string).
            ``None`` when ``kind == "none"``.
        command: Invocation hint surfaced in HARDEN.md. ``None`` when
            ``kind == "none"``.
    """

    kind: EntrypointKind
    name: str | None
    command: str | None


@dataclass(frozen=True)
class SoakSample:
    """One observation captured during the soak run.

    Attributes:
        sample_index: Zero-based ordinal within the soak run.
        elapsed_seconds: Seconds since runner start when the sample was
            taken. The last sample's value drives ``duration_seconds``.
        rss_bytes: Resident-set size in bytes at sample time.
        cpu_percent: CPU utilisation percentage at sample time.
    """

    sample_index: int
    elapsed_seconds: float
    rss_bytes: int
    cpu_percent: float


@dataclass(frozen=True)
class SoakResult:
    """Aggregate result of a soak run.

    Attributes:
        status: ``pass`` when the runner produced samples, no restarts
            occurred, and RSS growth stayed under the threshold;
            ``fail`` when restarts occurred or monotonic growth was
            flagged; ``skipped`` when no entrypoint exists or no runner
            was wired in.
        duration_seconds: Last sample's ``elapsed_seconds``, or 0.0 when
            skipped.
        entrypoint: Detected :class:`EntrypointInfo`.
        samples: Per-sample observations preserved in collection order.
        rss_peak_bytes: Maximum RSS across all samples (0 when skipped).
        cpu_peak_percent: Maximum CPU% across all samples (0.0 when
            skipped).
        restart_count: Restart events observed during the run.
        monotonic_growth_flagged: True iff
            ``(last.rss_bytes / first.rss_bytes) - 1.0 >= growth_threshold``.
        detail: One-line summary surfaced in HARDEN.md.
    """

    status: SoakStatus
    duration_seconds: float
    entrypoint: EntrypointInfo
    samples: list[SoakSample] = field(default_factory=list)
    rss_peak_bytes: int = 0
    cpu_peak_percent: float = 0.0
    restart_count: int = 0
    monotonic_growth_flagged: bool = False
    detail: str = ""


def _detect_python_script(repo_root: Path) -> EntrypointInfo | None:
    """Return a :class:`EntrypointInfo` from ``[project.scripts]`` or ``None``.

    First key in the ``[project.scripts]`` table wins. Returns ``None``
    when ``pyproject.toml`` is absent or has no scripts table — letting
    the caller fall through to ``package.json`` detection.
    """
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return None
    scripts = data.get("project", {}).get("scripts")
    if not isinstance(scripts, dict) or not scripts:
        return None
    name = next(iter(scripts))
    if not isinstance(name, str) or not name:
        return None
    return EntrypointInfo(kind="python_script", name=name, command=name)


def _resolve_bin_name(bin_entry: object, package_data: dict[str, object]) -> str | None:
    """Pull a usable bin name from a ``package.json`` ``"bin"`` value.

    Object form: first key wins. String form: falls back to the
    package's ``"name"`` field (npm's own convention for shorthand bins).
    Returns ``None`` for malformed or empty inputs.
    """
    if isinstance(bin_entry, dict) and bin_entry:
        first_key = next(iter(bin_entry))
        if isinstance(first_key, str) and first_key:
            return first_key
        return None
    if isinstance(bin_entry, str) and bin_entry:
        package_name = package_data.get("name")
        if isinstance(package_name, str) and package_name:
            return package_name
    return None


def _detect_node_bin(repo_root: Path) -> EntrypointInfo | None:
    """Return a :class:`EntrypointInfo` from ``package.json`` ``"bin"`` or ``None``.

    Handles both forms: ``"bin": "./cli.js"`` (package name becomes the
    bin name) and ``"bin": {"name": "./cli.js"}`` (first key wins).
    """
    package = repo_root / "package.json"
    if not package.is_file():
        return None
    try:
        data = json.loads(package.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    name = _resolve_bin_name(data.get("bin"), data)
    if name is None:
        return None
    return EntrypointInfo(kind="node_bin", name=name, command=name)


def detect_entrypoint(repo_root: Path) -> EntrypointInfo:
    """Inspect ``repo_root`` for a long-running entrypoint declaration.

    Resolution order: ``pyproject.toml`` ``[project.scripts]`` first,
    then ``package.json`` ``"bin"``. Returns ``EntrypointInfo(kind="none", ...)``
    when neither file declares one (libraries skip soak).
    """
    info = _detect_python_script(repo_root)
    if info is not None:
        return info
    info = _detect_node_bin(repo_root)
    if info is not None:
        return info
    return EntrypointInfo(kind="none", name=None, command=None)


def _default_runner(_info: EntrypointInfo, _soak_minutes: int) -> list[SoakSample]:
    """Return no samples — no real runner wired in.

    Keeps the module importable and exercisable without spawning a real
    subprocess. Real wiring lives in the harden orchestrator skill.
    """
    return []


def _default_restart_observer(_info: EntrypointInfo) -> int:
    """Return zero restarts — no real observer wired in."""
    return 0


def _is_monotonic_growth(samples: list[SoakSample], threshold: float) -> bool:
    """Flag when last-sample RSS exceeds first-sample RSS by ``threshold``.

    A first-sample RSS of zero suppresses the flag — division would be
    undefined and a zero baseline is almost certainly a measurement
    artefact, not a leak.
    """
    if len(samples) < _MIN_SAMPLES_FOR_GROWTH_CHECK:
        return False
    first_rss = samples[0].rss_bytes
    last_rss = samples[-1].rss_bytes
    if first_rss <= 0:
        return False
    return (last_rss / first_rss) - 1.0 >= threshold


def run_soak(
    repo_root: Path,
    feature_id: str,
    *,
    soak_minutes: int = DEFAULT_SOAK_MINUTES,
    growth_threshold: float = DEFAULT_MEMORY_GROWTH_THRESHOLD,
    runner: Callable[[EntrypointInfo, int], list[SoakSample]] | None = None,
    restart_observer: Callable[[EntrypointInfo], int] | None = None,
) -> SoakResult:
    """Run a soak check against a feature's merged artifact.

    Args:
        repo_root: Repository root the feature folder resolves under.
        feature_id: Feature identifier (e.g. ``2026-05-09-example``).
        soak_minutes: Target soak duration handed to the runner.
        growth_threshold: Fractional RSS-growth ceiling. Exceeding it
            flags ``monotonic_growth_flagged`` and folds to ``fail``.
        runner: Optional callable that performs the actual run and
            returns RSS/CPU samples. When omitted, a no-op default
            returns ``[]`` and the result folds to ``skipped`` so the
            record never claims confidence the soak did not earn.
        restart_observer: Optional callable returning the number of
            restart events observed during the run. Defaults to a
            zero-returning observer.

    Returns:
        :class:`SoakResult` with samples, peak metrics, and aggregate
        status.

    Raises:
        HardenError: If the feature's SPEC.md is missing.
    """
    spec_path = repo_root / ".forge" / "features" / feature_id / "SPEC.md"
    spec_text = _read_text(spec_path)
    if spec_text is None:
        raise HardenError(f"SPEC.md missing for feature {feature_id!r} at {spec_path}")

    entrypoint = detect_entrypoint(repo_root)

    if entrypoint.kind == "none":
        return SoakResult(
            status="skipped",
            duration_seconds=0.0,
            entrypoint=entrypoint,
            samples=[],
            rss_peak_bytes=0,
            cpu_peak_percent=0.0,
            restart_count=0,
            monotonic_growth_flagged=False,
            detail=_NO_ENTRYPOINT_DETAIL,
        )

    active_runner = runner if runner is not None else _default_runner
    active_observer = (
        restart_observer if restart_observer is not None else _default_restart_observer
    )

    samples = list(active_runner(entrypoint, soak_minutes))

    if not samples:
        return SoakResult(
            status="skipped",
            duration_seconds=0.0,
            entrypoint=entrypoint,
            samples=[],
            rss_peak_bytes=0,
            cpu_peak_percent=0.0,
            restart_count=0,
            monotonic_growth_flagged=False,
            detail=_DEFAULT_RUNNER_DETAIL,
        )

    rss_peak = max(sample.rss_bytes for sample in samples)
    cpu_peak = max(sample.cpu_percent for sample in samples)
    growth_flagged = _is_monotonic_growth(samples, growth_threshold)
    restart_count = active_observer(entrypoint)
    duration = samples[-1].elapsed_seconds

    if restart_count > 0 or growth_flagged:
        status: SoakStatus = "fail"
        reasons: list[str] = []
        if restart_count > 0:
            reasons.append(f"{restart_count} restart event(s)")
        if growth_flagged:
            reasons.append(
                f"RSS growth >= {growth_threshold:.0%} "
                f"({samples[0].rss_bytes} -> {samples[-1].rss_bytes} bytes)"
            )
        detail = "; ".join(reasons)
    else:
        status = "pass"
        detail = (
            f"{len(samples)} samples over {duration:.1f}s, "
            f"peak RSS {rss_peak} bytes, peak CPU {cpu_peak:.1f}%"
        )

    return SoakResult(
        status=status,
        duration_seconds=duration,
        entrypoint=entrypoint,
        samples=samples,
        rss_peak_bytes=rss_peak,
        cpu_peak_percent=cpu_peak,
        restart_count=restart_count,
        monotonic_growth_flagged=growth_flagged,
        detail=detail,
    )

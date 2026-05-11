"""Capstone parity walker for the additive substrate work.

Confirms the substrate is **purely additive**: walking every tracked
``state.json`` and round-tripping it through ``read_state`` →
``get_phase_list`` → ``write_state`` produces the same on-disk bytes as
a plain ``read_state`` → ``write_state`` round-trip without the
accessor. Two-round-trip equality (rather than byte-equality against the
git-tracked source) is the same reframe applied by
``tests/tools/test_state_no_phase_list_writeback.py``: some hand-authored
fixtures use compact one-line blocks that ``write_state``'s
``json.dump(..., indent=2)`` re-formats. That drift pre-dates the
substrate work and lives in the fixture, not in the accessor; comparing
the two round-trips against each other isolates the contract under test.

Also asserts that ``read_state`` does not inject ``routing.phase_list``
into the parsed payload when the on-disk file does not carry that key.

The tracked-fixture walker and exclusion list live in
``tests/_helpers/state_fixtures.py``; this file shares them with the
unit-level no-write-back test.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tests._helpers.state_fixtures import tracked_state_files
from tools.state import get_phase_list, read_state, write_state


def test_tracked_state_files_set_is_non_empty(repo_root: Path) -> None:
    """Sanity: at least one tracked state.json exists, otherwise the loop is hollow."""
    files = tracked_state_files(repo_root)
    assert files, "expected at least one tracked state.json after exclusions"


def test_substrate_round_trip_matches_baseline_round_trip(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    """Per-file: round-trip through ``get_phase_list`` matches a plain
    read/write round-trip byte-for-byte. Any divergence implies the
    accessor introduced a write-back side effect.
    """
    failures: list[str] = []
    for source in tracked_state_files(repo_root):
        rel = source.relative_to(repo_root)

        # Baseline pass: read_state -> write_state, no accessor.
        baseline_target = tmp_path / "baseline" / rel
        baseline_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, baseline_target)
        baseline_payload = read_state(baseline_target)
        write_state(baseline_target, baseline_payload)

        # Accessor pass: read_state -> get_phase_list -> write_state.
        accessor_target = tmp_path / "accessor" / rel
        accessor_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, accessor_target)
        accessor_payload = read_state(accessor_target)
        _ = get_phase_list(accessor_payload)
        write_state(accessor_target, accessor_payload)

        if accessor_target.read_bytes() != baseline_target.read_bytes():
            failures.append(str(rel))

    if failures:
        pytest.fail("get_phase_list introduced write-back drift on:\n" + "\n".join(failures))


def test_read_state_does_not_inject_phase_list_when_absent_on_disk(
    repo_root: Path,
) -> None:
    """For every tracked state.json without an on-disk ``routing.phase_list``,
    the parsed payload returned by ``read_state`` (and unchanged by a
    subsequent ``get_phase_list`` call) must also lack that key.
    """
    failures: list[str] = []
    for source in tracked_state_files(repo_root):
        on_disk = json.loads(source.read_text(encoding="utf-8"))
        on_disk_routing = on_disk.get("routing")
        if isinstance(on_disk_routing, dict) and "phase_list" in on_disk_routing:
            # File explicitly carries the field; parity test above covers
            # write-back behavior.
            continue

        payload = read_state(source)
        _ = get_phase_list(payload)
        routing = payload.get("routing")
        if isinstance(routing, dict) and "phase_list" in routing:
            failures.append(str(source.relative_to(repo_root)))

    if failures:
        pytest.fail(
            "read_state injected routing.phase_list into payloads that had no "
            "such field on disk:\n" + "\n".join(failures)
        )

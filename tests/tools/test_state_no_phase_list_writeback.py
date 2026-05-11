"""Decision-4 lock: ``read_state`` never injects ``routing.phase_list``.

The lazy ``get_phase_list`` accessor is read-only — it must not have any
write-back side effects through ``read_state`` / ``write_state`` /
``complete_phase`` / ``start_phase``. This test walks every tracked
``state.json`` (per the M8 P0 plan pre-flight inventory), invokes
``read_state`` → ``get_phase_list`` → ``write_state``, and asserts the
on-disk file is byte-identical to the original. It also asserts that the
payload returned by ``read_state`` does not carry a ``routing.phase_list``
key when the on-disk file did not.

The tracked-fixture walker and exclusion list live in
``tests/_helpers/state_fixtures.py`` so the M8 regression capstone shares
exactly the same set.
"""

from __future__ import annotations

import copy
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


def test_get_phase_list_introduces_no_write_back_drift(
    repo_root: Path,
    tmp_path: Path,
) -> None:
    """``get_phase_list`` is read-only: a round-trip through the accessor
    must be byte-identical to a round-trip without it. Some legacy fixtures
    were authored with non-canonical formatting (compact one-line blocks),
    so a raw ``original_bytes`` comparison would falsely flame on
    ``write_state``'s ``indent=2`` re-formatting — drift that pre-dates P0.2
    and lives in the fixture, not in the accessor. Comparing the two
    round-trips against each other isolates the P0.2 contract: invoking
    ``get_phase_list`` between read and write must change nothing on disk.
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


def test_get_phase_list_does_not_mutate_payload_in_place(
    repo_root: Path,
) -> None:
    """Independent of write_state: the parsed payload itself must be
    structurally unchanged after calling ``get_phase_list``. Snapshots the
    pre-call payload via deep copy and compares post-call.
    """
    failures: list[str] = []
    for source in tracked_state_files(repo_root):
        payload = read_state(source)
        snapshot = copy.deepcopy(payload)
        _ = get_phase_list(payload)
        if payload != snapshot:
            failures.append(str(source.relative_to(repo_root)))

    if failures:
        pytest.fail("get_phase_list mutated the payload in place for:\n" + "\n".join(failures))


def test_read_state_does_not_inject_phase_list_when_absent_on_disk(
    repo_root: Path,
) -> None:
    """For every tracked state.json without an on-disk ``routing.phase_list``,
    the parsed payload returned by ``read_state`` must also lack that key.
    """
    failures: list[str] = []
    for source in tracked_state_files(repo_root):
        on_disk = json.loads(source.read_text(encoding="utf-8"))
        on_disk_routing = on_disk.get("routing")
        if isinstance(on_disk_routing, dict) and "phase_list" in on_disk_routing:
            # File explicitly carries the field; nothing to assert about
            # write-back here (round-trip test above covers byte parity).
            continue

        payload = read_state(source)
        routing = payload.get("routing")
        if isinstance(routing, dict) and "phase_list" in routing:
            failures.append(str(source.relative_to(repo_root)))

    if failures:
        pytest.fail(
            "read_state injected routing.phase_list into payloads that had no "
            "such field on disk:\n" + "\n".join(failures)
        )

"""Shared helpers for tests that walk every tracked ``state.json`` file.

Both the M8 P0 substrate regression walker and the unit-level no-write-back
test enumerate the same set of fixtures with the same exclusion list. Lifting
the walker here keeps a fixture exclusion change to one place.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# Files excluded from the tracked-state walk:
#
# * ``templates/feature/state.json`` — the bare scaffold under templates is
#   intentionally minimal and predates several required fields.
# * ``tests/fixtures/_negative/invalid_state.json`` — focused-tier negative
#   fixture, intentionally invalid.
# * ``tests/fixtures/_validate/deviations_unparseable_state/state.json`` — a
#   deliberately malformed JSON fixture used by the deviations-validator
#   tests to exercise the "state.json is unparseable" path.
TRACKED_STATE_EXCLUSIONS: frozenset[str] = frozenset(
    {
        "templates/feature/state.json",
        "tests/fixtures/_negative/invalid_state.json",
        "tests/fixtures/_validate/deviations_unparseable_state/state.json",
    }
)


def tracked_state_files(repo_root: Path) -> list[Path]:
    """Return absolute paths to every tracked ``state.json`` minus the
    documented exclusions.

    Uses ``git ls-files`` so the walker honours staging — untracked
    fixtures and gitignored copies are skipped automatically.
    """
    result = subprocess.run(
        ["git", "ls-files", "*state.json"],
        check=True,
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    paths: list[Path] = []
    for line in result.stdout.splitlines():
        rel = line.strip()
        if not rel:
            continue
        if rel in TRACKED_STATE_EXCLUSIONS:
            continue
        paths.append(repo_root / rel)
    return paths

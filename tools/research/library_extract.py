"""Canonical library-name normalization for the research phase.

Library names arrive from three independent sources:

* Manifest-declared deps (per ecosystem plugin) — case and separator
  conventions vary by ecosystem (e.g. ``My-Lib`` in ``pyproject.toml``,
  ``my_lib`` after ``importlib.metadata`` normalises it, ``my-lib`` in
  npm).
* Import-scan results from `scan_imports()` — typically lowercase already.
* BYOD filenames under ``.forge/external-docs/`` — user-staged, case
  unspecified.

To match across these surfaces we collapse to a single canonical form:
lowercase + hyphens replaced with underscores. Idempotent.

This module intentionally has zero external deps: it is imported from
both the ecosystem detector and the grounding-mode resolver, and we
keep that lower layer free of optional surfaces.
"""

from collections.abc import Iterable


def normalize(name: str) -> str:
    """Return the canonical lowercase + underscore form of ``name``."""
    return name.lower().replace("-", "_")


def dedup(names: Iterable[str]) -> list[str]:
    """Return unique normalized ``names`` preserving first-seen order."""
    seen: dict[str, None] = {}
    for raw in names:
        canon = normalize(raw)
        if canon not in seen:
            seen[canon] = None
    return list(seen)

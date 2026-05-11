"""Ecosystem plugin Protocol + registry walker for the research phase.

The research phase needs a single, abstract way to ask "what package
managers / build tools live at this repo root, and what manifests should
the subagent read?". Hard-coding ``pyproject.toml`` / ``package.json`` /
etc. into skill prose is brittle (skill prose tests would break every
time we add a language) and forces the prose to enumerate languages it
does not understand. Instead: each supported language ships a small
plugin module under ``tools.research.ecosystems``; the plugin tells the
detector which root-marker files to look for and which manifests to
hand back if it matches; the skill defers to the abstract list.

This module defines the public contract:

* :class:`Ecosystem` — runtime-checkable Protocol every plugin satisfies.
* :class:`EcosystemRecord` — the frozen record returned from a successful
  ``match()`` call. The detector returns these (not the live plugin
  object) so callers cannot accidentally mutate plugin state.
* :func:`detect` — walks the registered plugins, calls ``match()`` on
  each, and returns the matching records sorted deterministically by
  ``(priority, name)``.

Pinning semantics
-----------------

Callers may pass ``pinned=["python", "node"]`` to restrict detection to
a known subset. **Unknown pin names are silently filtered** to the set
of registered plugins — they are not an error. Rationale: a forge
config that lists ``pinned: ["python"]`` should remain valid even if
the python plugin has not yet been registered (e.g. fresh repo where
the registry has only the generic fallback). Loud failure here would
block the research phase exactly when the safety net (generic
fallback) is most useful.

Generic-fallback semantics
--------------------------

When ``pinned`` is ``None`` and no concrete plugin matches, the walker
falls back to the generic ecosystem so the research phase never
returns an empty list to the subagent. When ``pinned`` is supplied
(even if no pin name resolves to a registered plugin), the walker
honors the explicit caller intent and returns the empty list — the
caller asked for a specific subset.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class EcosystemRecord:
    """Concrete record returned by :meth:`Ecosystem.match`.

    Attributes:
        name: Lowercase ecosystem identifier (e.g. ``"python"``,
            ``"node"``, ``"generic"``). Used for deterministic ordering
            and as the pin-list key.
        priority: Sort key. Lower values appear earlier in the result
            list. Concrete plugins use ``10``; the generic fallback
            uses ``99`` so it always sorts last.
        manifest_paths: Repo-relative paths to the manifest files this
            ecosystem owns (e.g. ``("pyproject.toml",)``). Tuple keeps
            the dataclass hashable.
        declared_deps: Snapshot of the lowercase dependency names this
            ecosystem declared at detection time, or empty when the
            plugin defers parsing to its ``scan_imports`` callback.
        standard_dirs: Mapping from directory role (``"test"`` /
            ``"source"``) to a tuple of repo-relative directory names
            the ecosystem treats as canonical for that role.
    """

    name: str
    priority: int
    manifest_paths: tuple[str, ...]
    declared_deps: tuple[str, ...]
    standard_dirs: dict[str, tuple[str, ...]]


@runtime_checkable
class Ecosystem(Protocol):
    """Contract every ecosystem plugin must implement.

    Plugins live under ``tools.research.ecosystems`` and expose a
    module-level ``plugin = MyEcosystem()`` instance. The detector
    iterates the registry list, calls :meth:`match` on each plugin, and
    aggregates the non-``None`` results.

    The Protocol is ``runtime_checkable`` so tests can assert that a
    plugin (or a stub) satisfies the contract via ``isinstance(p,
    Ecosystem)``. The check verifies attribute presence, not signature
    shape — sticking to the documented signatures is the plugin
    author's responsibility.
    """

    name: str
    """Lowercase ecosystem identifier."""

    def match(self, repo_root: Path) -> EcosystemRecord | None:
        """Return a populated record if the ecosystem matches this repo.

        Implementations inspect only root-level marker files (no
        recursion) so the call stays cheap. Return ``None`` to signal
        "not present"; do not raise.
        """
        ...

    def scan_imports(self, repo_root: Path) -> list[str]:
        """Return deduped, lowercase import names found in the repo.

        Best-effort. Failures should be swallowed and an empty list
        returned. The Context7/WebSearch lookup is the authoritative
        source for what to research; this list is supplementary
        evidence used to enrich the lookup.
        """
        ...


def _load_plugins() -> list[Ecosystem]:
    """Return the registered plugin instances.

    Indirected through a function so tests can monkeypatch the registry
    without reaching into the ``ecosystems`` package directly. The
    function does the import lazily so importing this module never
    triggers loading every plugin.
    """
    from tools.research.ecosystems import PLUGINS  # noqa: PLC0415  (circular import guard)

    return list(PLUGINS)


def detect(repo_root: Path, *, pinned: list[str] | None = None) -> list[EcosystemRecord]:
    """Walk the plugin registry and return matching records.

    Args:
        repo_root: Path to the repository root the detector inspects.
            Plugins look at root-level marker files only (no recursion).
        pinned: Optional list of ecosystem names to restrict the walk
            to. Unknown names are silently filtered to the set of
            registered plugins — not an error. When omitted (``None``),
            every registered plugin is given the chance to match, and
            an empty result triggers the generic fallback.

    Returns:
        List of :class:`EcosystemRecord` sorted by ``(priority, name)``
        for stable, polyglot-friendly output. When ``pinned`` is
        ``None`` and no plugin matched, returns the generic fallback as
        a single-element list.
    """
    plugins = _load_plugins()
    if pinned is not None:
        wanted = set(pinned)
        plugins = [p for p in plugins if p.name in wanted]

    records: list[EcosystemRecord] = []
    for plugin in plugins:
        record = plugin.match(repo_root)
        if record is not None:
            records.append(record)

    if not records and pinned is None:
        from tools.research.ecosystems import (  # noqa: PLC0415  (circular import guard)
            generic as generic_module,
        )

        fallback = generic_module.plugin.match(repo_root)
        if fallback is not None:
            records.append(fallback)

    records.sort(key=lambda r: (r.priority, r.name))
    return records

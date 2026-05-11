"""Grounding-mode resolver for the research phase.

This is the single decision-point that maps the subagent's runtime
probe + the user's config + the canonical library list + the optional
BYOD directory to one of the five grounding modes the rest of the
research surface understands.

The function is **pure**. It never raises and never spawns a
subprocess. The only filesystem touch is ``byod_dir.glob("*.md")``
when a BYOD directory is supplied.

Spec reference: §5.3.9. The v1 simplification noted on rule 1 is
documented in the docstring below.
"""

from collections.abc import Mapping
from pathlib import Path

from tools.research.library_extract import normalize


def resolve_mode(
    probe: Mapping[str, object],
    config: Mapping[str, object],
    libraries_extracted: tuple[str, ...],
    byod_dir: Path | None,
) -> str:
    """Resolve the grounding mode.

    Decision tree (spec §5.3.9):

    1. ``probe.context7_callable`` truthy → ``"full"``.

       *v1 simplification:* the spec couples this to "any successful
       context7 lookup". In this layer we only have the probe; the
       per-lookup ``[context7:UNAVAILABLE]`` annotation is the
       responsibility of the citation validator and the subagent prose,
       not this resolver. Treating ``context7_callable`` as authoritative
       here keeps the resolver pure and side-effect free.

    2. ``byod_dir`` non-None: enumerate ``*.md`` files, normalize each
       basename via :func:`tools.research.library_extract.normalize`,
       and compare against ``libraries_extracted``:

       * all libs covered → ``"byod"``
       * ≥1 covered + ≥1 missing → ``"byod-partial"``
       * none covered → fall through to step 3.

    3. ``config["research"]["websearch_fallback"]`` truthy AND
       ``probe["websearch_present"]`` truthy → ``"websearch"``. The
       resolver accepts both the root ``.forge/config.json`` shape
       (``{"research": {"websearch_fallback": true}}``, the documented
       form) and the flat ``{"websearch_fallback": true}`` shape used by
       callers that pre-extracted the research sub-block. Root shape is
       checked first; flat is the legacy fallback.

    4. Otherwise → ``"degraded"``.

    Returns one of ``"full" | "degraded" | "websearch" | "byod" |
    "byod-partial"``. Never raises.
    """
    if probe.get("context7_callable"):
        return "full"

    if byod_dir is not None:
        covered = _byod_coverage(byod_dir, libraries_extracted)
        total = len(libraries_extracted)
        if total > 0 and covered == total:
            return "byod"
        if covered > 0:
            return "byod-partial"

    if _websearch_enabled(config) and probe.get("websearch_present"):
        return "websearch"

    return "degraded"


def _websearch_enabled(config: Mapping[str, object]) -> bool:
    """Return True when the config opts into the WebSearch fallback.

    Checks ``config["research"]["websearch_fallback"]`` first (documented
    ``.forge/config.json`` shape) and falls back to the flat
    ``config["websearch_fallback"]`` for callers that already extracted
    the research sub-block.
    """
    research_block = config.get("research")
    if isinstance(research_block, Mapping) and research_block.get("websearch_fallback"):
        return True
    return bool(config.get("websearch_fallback"))


def _byod_coverage(byod_dir: Path, libraries: tuple[str, ...]) -> int:
    if not libraries:
        return 0
    try:
        staged = {normalize(p.stem) for p in byod_dir.glob("*.md")}
    except OSError:
        return 0
    canonical_libs = {normalize(lib) for lib in libraries}
    return len(canonical_libs & staged)

r"""Semantic validators for SPEC.md (M3 §5.3.6 D-8 scenarios↔acceptance, P2b).

Section parsing strategy (corrections #1, #2, #4 from pre-execution review):

- Acceptance Criteria indices are extracted from the body slice between
  ``^# Acceptance Criteria\\b`` and the next ``^# `` heading. Numbered lists
  in Open Questions / Test Strategy are NOT ACs.
- Scenario blocks are extracted from the body slice between ``^# Scenarios\\b``
  (also matches ``# Scenarios (BDD)``) and the next ``^# `` heading.
  **The slice is parsed verbatim — `_strip_code` is NOT applied here**
  because the SPEC template ships Gherkin inside a ```gherkin fence and
  whitespace-padding the fence body would erase the scenarios. Future
  maintainers: do not unify this with the NR / weasel-word default path
  that runs `_strip_code` — fenced code is decoration there, content here.
- AC reference tokens are explicit: ``crit-N``, ``criterion-N``,
  ``criterion: N``, ``Scenario N`` (case-insensitive). Bare digits (e.g.
  ``OAuth 2 login``) do NOT match.
"""

from __future__ import annotations

import re
from pathlib import Path

from ._finding import Finding
from ._frontmatter import _read_text

_SCENARIOS_BLOCK = re.compile(r"(?ms)^# Scenarios\b[^\n]*\n(?P<body>.*?)(?=^# |\Z)")
_ACCEPTANCE_BLOCK = re.compile(r"(?ms)^# Acceptance Criteria\b[^\n]*\n(?P<body>.*?)(?=^# |\Z)")
_AC_NUMBERED = re.compile(r"^(\d+)\.\s+(.+)$", re.MULTILINE)
_MEASURABLE_TOKEN = re.compile(r"\(measurable\)\s*$", re.IGNORECASE)
_FENCE_DELIM = re.compile(r"^```")
_GHERKIN_TITLE = re.compile(r"^Scenario:\s*(?P<title>.+?)\s*$", re.MULTILINE)
_WEASEL_WORDS: dict[str, re.Pattern[str]] = {
    "should": re.compile(r"\bshould\b", re.IGNORECASE),
    "might": re.compile(r"\bmight\b", re.IGNORECASE),
    "TBD": re.compile(r"\bTBD\b"),  # uppercase token only
}

_ANCHORS_BLOCK = re.compile(r"(?ms)^#{1,2} Codebase Anchors\b[^\n]*\n(?P<body>.*?)(?=^#{1,2} |\Z)")
_ANCHOR_ROW = re.compile(
    r"^[-*]\s+`(?P<path>[^`:]+?)(?::(?P<symbol>[^`]+))?`",
    re.MULTILINE,
)


def _anchor_path_within_repo(repo_root: Path, raw: str) -> Path | None:
    """Resolve raw anchor path under repo_root.

    Reject absolute paths and ``..`` traversal that escapes the repo root.
    Returns ``None`` on rejection so the caller can surface a BLOCK finding.
    """
    raw = raw.strip()
    candidate = Path(raw)
    if candidate.is_absolute():
        return None
    repo_root_resolved = repo_root.resolve()
    resolved = (repo_root_resolved / candidate).resolve()
    try:
        resolved.relative_to(repo_root_resolved)
    except ValueError:
        return None
    return resolved


def _extract_scenarios_slice(text: str) -> str | None:
    """Return raw scenarios body slice (fences preserved) or None."""
    match = _SCENARIOS_BLOCK.search(text)
    return match.group("body") if match else None


def _extract_gherkin_payload(slice_body: str) -> str:
    """Drop fence delimiter lines, preserve everything else.

    Walks the slice line-by-line. Lines inside ``` ```gherkin``` ``` (or any
    ``` ``` ``` ```) blocks AND lines outside fences both become part of the
    payload — only the fence delimiter line itself is dropped. This keeps the
    template-shaped SPEC (Gherkin inside a fence) on the same code path as
    inline ``Scenario:`` lines.
    """
    out: list[str] = []
    for line in slice_body.splitlines():
        stripped = line.lstrip()
        if _FENCE_DELIM.match(stripped):
            continue
        out.append(line)
    return "\n".join(out)


def _parse_scenario_blocks(payload: str) -> list[tuple[str, str]]:
    """Return (title, body) tuples per Scenario block.

    Body runs from the line after ``Scenario: <title>`` to the next
    ``Scenario:`` line or end of payload.
    """
    matches = list(_GHERKIN_TITLE.finditer(payload))
    blocks: list[tuple[str, str]] = []
    for i, match in enumerate(matches):
        title = match.group("title")
        body_start = match.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(payload)
        blocks.append((title, payload[body_start:body_end]))
    return blocks


def _ac_indices_with_measurable_flag(spec_text: str) -> list[tuple[int, bool]]:
    """Return (index, is_measurable) for each AC under `# Acceptance Criteria`."""
    block = _ACCEPTANCE_BLOCK.search(spec_text)
    if block is None:
        return []
    out: list[tuple[int, bool]] = []
    for match in _AC_NUMBERED.finditer(block.group("body")):
        idx = int(match.group(1))
        text = match.group(2).strip()
        out.append((idx, bool(_MEASURABLE_TOKEN.search(text))))
    return out


def _ac_token_pattern(idx: int) -> re.Pattern[str]:
    return re.compile(
        rf"(?:\bcrit-{idx}\b|\bcriterion-{idx}\b|\bcriterion:\s*{idx}\b|\bScenario\s+{idx}\b)",
        re.IGNORECASE,
    )


def validate_scenarios(path: Path) -> list[Finding]:
    """Check scenario↔acceptance mapping in a SPEC.md.

    Migrates the ``idd-scenarios`` self-review block
    (``skills/idd-scenarios/SKILL.md:36-40``).

    Rules:
        1. SPEC must declare ``# Scenarios`` (BLOCK if absent).
        2. Every numbered Acceptance criterion (extracted from the
           ``# Acceptance Criteria`` section only) has ≥1 scenario block whose
           title or body contains ``crit-N`` / ``criterion-N`` /
           ``criterion: N`` / ``Scenario N`` (HIGH). ACs ending with
           ``(measurable)`` are exempt per ``skills/idd-spec/SKILL.md:34``.
        3. Every scenario block references ≥1 Acceptance criterion (HIGH;
           orphan).
        4. No scenario block (title OR Given/When/Then body) contains the
           weasel words ``should`` / ``might`` / ``TBD`` (MEDIUM). ``should``
           and ``might`` are case-insensitive; ``TBD`` is uppercase-only.

    Args:
        path: Path to the SPEC.md file.

    Returns:
        List of Finding records. Empty list means scenarios↔acceptance
        mapping is clean.
    """
    text = _read_text(path)
    if text is None:
        return [Finding("BLOCK", "scenarios", path, f"missing or unreadable: {path}")]

    scenarios_slice = _extract_scenarios_slice(text)
    if scenarios_slice is None:
        return [Finding("BLOCK", "scenarios", path, "missing `# Scenarios` section")]

    payload = _extract_gherkin_payload(scenarios_slice)
    blocks = _parse_scenario_blocks(payload)
    ac_entries = _ac_indices_with_measurable_flag(text)

    findings: list[Finding] = []

    for idx, is_measurable in ac_entries:
        if is_measurable:
            continue  # exempt per skills/idd-spec/SKILL.md:34
        pat = _ac_token_pattern(idx)
        if not any(pat.search(title) or pat.search(body) for title, body in blocks):
            findings.append(
                Finding(
                    "HIGH",
                    "scenarios",
                    path,
                    f"AC {idx} has no scenario referencing it",
                ),
            )

    for title, body in blocks:
        scenario_text = f"{title}\n{body}"
        if not any(_ac_token_pattern(idx).search(scenario_text) for idx, _ in ac_entries):
            findings.append(
                Finding(
                    "HIGH",
                    "scenarios",
                    path,
                    f"orphan scenario {title!r} maps to no AC",
                ),
            )
        for word, pattern in _WEASEL_WORDS.items():
            if pattern.search(scenario_text):
                findings.append(
                    Finding(
                        "MEDIUM",
                        "scenarios",
                        path,
                        f"scenario {title!r} contains weasel word {word!r}",
                    ),
                )

    return findings


def validate_anchors(path: Path, *, repo_root: Path) -> list[Finding]:
    """Resolve ``path:Symbol`` rows in SPEC ``# Codebase Anchors`` against repo_root.

    Migrates ``skills/idd-spec/SKILL.md:30`` — Codebase Anchors must point at
    real files and (where supplied) real symbols.

    Rules:
        1. SPEC missing ``# Codebase Anchors`` -> no findings (anchors are
           optional). Heading accepts H1 or H2.
        2. Anchor path absolute or escaping ``repo_root`` via ``..`` -> BLOCK
           (path-traversal guard).
        3. Anchor path resolves under ``repo_root`` but is not a regular file
           -> HIGH (the anchor lies).
        4. Path resolves and ``:Symbol`` is supplied but the symbol token is
           not present in the file (word-boundary regex) -> MEDIUM (drift /
           rename).
        5. Module-only rows (``pkg/mod.py`` with no ``:Symbol``) skip the
           symbol step -- path-only check.

    Args:
        path: Path to the SPEC.md file.
        repo_root: Repository root that anchor paths resolve against.

    Returns:
        List of Finding records. Empty list means every Codebase Anchor row
        resolves cleanly (or the section is absent).
    """
    text = _read_text(path)
    if text is None:
        return [Finding("BLOCK", "anchors", path, f"missing or unreadable: {path}")]

    block_match = _ANCHORS_BLOCK.search(text)
    if block_match is None:
        return []

    findings: list[Finding] = []
    for row in _ANCHOR_ROW.finditer(block_match.group("body")):
        raw_path = row.group("path").strip()
        anchor_path = _anchor_path_within_repo(repo_root, raw_path)
        if anchor_path is None:
            findings.append(
                Finding(
                    "BLOCK",
                    "anchors",
                    path,
                    f"anchor path escapes repo_root or is absolute: {raw_path!r}",
                )
            )
            continue
        if not anchor_path.is_file():
            findings.append(
                Finding(
                    "HIGH",
                    "anchors",
                    path,
                    f"anchor path not found in repo: {raw_path}",
                )
            )
            continue
        symbol = row.group("symbol")
        if symbol is None:
            continue
        symbol = symbol.strip()
        try:
            file_text = anchor_path.read_text(encoding="utf-8")
        except OSError as exc:  # pragma: no cover - defensive
            findings.append(
                Finding(
                    "MEDIUM",
                    "anchors",
                    path,
                    f"could not read anchor file {anchor_path}: {exc}",
                )
            )
            continue
        if not re.search(rf"\b{re.escape(symbol)}\b", file_text):
            findings.append(
                Finding(
                    "MEDIUM",
                    "anchors",
                    path,
                    f"anchor symbol {symbol!r} not found in {raw_path}",
                )
            )

    return findings

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

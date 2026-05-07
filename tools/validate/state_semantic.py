"""Semantic validators that cross-reference state.json against sibling artifacts.

Corrections #15, #20, #21 from pre-execution review:
- Decision heading separator accepts hyphen, en-dash, em-dash glyph variants.
- If state has non-empty deviations[] but decisions.md is missing or empty,
  emit BLOCK pointing at decisions.md (not HIGH per-deviation - root cause is
  the missing file, not the un-recorded entries).
- Cause normalization order: strip -> lower -> slice (preserves trailing
  meaningful chars instead of clipping whitespace).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ._feature_layout import DECISIONS_FILENAME, STATE_FILENAME
from ._finding import Finding
from ._frontmatter import _read_text

# Accept ASCII hyphen-minus, U+2013 EN DASH, U+2014 EM DASH. Authors paste any.
# Unicode escapes (not literal glyphs) keep ruff RUF001 happy.
_DASH_CLASS = "[\u2014\u2013-]"
_DECISION_HEADING = re.compile(
    r"(?ms)^## (?P<date>\d{4}-\d{2}-\d{2})\s+" + _DASH_CLASS + r"\s+(?P<title>[^\n]+)$"
    r"(?P<body>.*?)(?=^## \d{4}-\d{2}-\d{2}\s+" + _DASH_CLASS + r"\s+|\Z)"
)


class _DeviationsParseError(RuntimeError):
    """Raised when state.json deviations[] cannot be processed.

    Carries a precise message so the caller can surface it verbatim in the
    BLOCK finding instead of the generic "missing or malformed" string.
    """


def _load_state_deviations(state_path: Path) -> list[dict[str, Any]]:
    """Return the deviations[] list. Raise on any structural problem.

    Pre-fix this helper silently dropped non-dict deviation entries (e.g.
    ``{"deviations": ["bad"]}`` collapsed to ``[]``). Now any non-object
    entry raises so the caller emits a BLOCK — ``/idd:execute`` migrated to
    delegate its self-review to this validator, and a silently-passing
    malformed deviation would let the phase exit unreviewed.
    """
    text = _read_text(state_path)
    if text is None:
        raise _DeviationsParseError("state.json missing or unreadable")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise _DeviationsParseError(f"state.json invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise _DeviationsParseError("state.json must be a JSON object")
    deviations = payload.get("deviations")
    if not isinstance(deviations, list):
        raise _DeviationsParseError("state.json deviations must be a list")
    for i, entry in enumerate(deviations):
        if not isinstance(entry, dict):
            raise _DeviationsParseError(
                f"state.json deviations[{i}] must be an object, "
                f"got {type(entry).__name__}: {entry!r}"
            )
    return deviations


def validate_deviations(feature_root: Path) -> list[Finding]:
    """Cross-reference state.json deviations[] against decisions.md headings."""
    state_path = feature_root / STATE_FILENAME
    decisions_path = feature_root / DECISIONS_FILENAME

    try:
        deviations = _load_state_deviations(state_path)
    except _DeviationsParseError as exc:
        return [Finding("BLOCK", "deviations", state_path, str(exc))]

    if not deviations:
        return []  # Empty deviations[] is fine; nothing to cross-ref.

    decisions_text = _read_text(decisions_path)
    if decisions_text is None or not decisions_text.strip():
        return [
            Finding(
                "BLOCK",
                "deviations",
                decisions_path,
                f"state.json declares {len(deviations)} deviation(s) but "
                f"decisions.md is missing or empty; cannot cross-reference",
            )
        ]

    decisions = list(_DECISION_HEADING.finditer(decisions_text))

    findings: list[Finding] = []
    for dev in deviations:
        raw_cause = str(dev.get("cause", "")).strip().lower()
        cause = raw_cause[:60]
        if not cause:
            findings.append(
                Finding(
                    "HIGH",
                    "deviations",
                    state_path,
                    f"deviation entry {dev!r} has empty cause",
                )
            )
            continue
        if not any(
            cause in m.group("body").lower() or cause in m.group("title").lower() for m in decisions
        ):
            findings.append(
                Finding(
                    "HIGH",
                    "deviations",
                    decisions_path,
                    f"deviation cause {cause!r} not recorded in decisions.md",
                )
            )

    state_phases = {str(d.get("phase", "")).lower() for d in deviations}
    for m in decisions:
        title = m.group("title").lower()
        if "phase=" in title:
            phase = title.split("phase=", 1)[1].split()[0].strip(":,)")
            if phase and phase not in state_phases:
                findings.append(
                    Finding(
                        "INFO",
                        "deviations",
                        decisions_path,
                        f"decision references phase {phase!r} not in state.json deviations",
                    )
                )

    return findings

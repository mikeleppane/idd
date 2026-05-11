"""Pin the ``# Findings`` heading on the REVIEW template.

``tools.cross_ai.manual.merge_findings_into_review`` locates its
insertion point by scanning for a ``# Findings`` heading. The matcher
tolerates depth (``##`` / ``###``) and trailing suffixes
(``# Findings (cycle 2)``), but the *first word* after the hash run
must be exactly ``findings`` (case-insensitive). This regression
guards against a future template rename (e.g. to ``# Issues`` or
``# Defects``) silently breaking the merge helper.
"""

from __future__ import annotations

from pathlib import Path

_TEMPLATE = Path("templates/feature/REVIEW.md")


def test_review_template_carries_findings_heading() -> None:
    body = _TEMPLATE.read_text(encoding="utf-8")
    assert "\n# Findings\n" in body or body.startswith("# Findings\n"), (
        "REVIEW.md template must carry a '# Findings' heading verbatim — "
        "tools.cross_ai.manual.merge_findings_into_review depends on it "
        "to locate the table insertion point"
    )

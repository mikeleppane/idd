"""REVIEW.md template MUST carry a Status column so the §5.3.9 ship gate
can distinguish unresolved findings from convergence-loop history."""

from __future__ import annotations

from pathlib import Path

TEMPLATE = Path("templates/feature/REVIEW.md")


def test_review_template_has_status_column() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    header = next(
        (line for line in text.splitlines() if line.startswith("| ID |")),
        None,
    )
    assert header is not None, (
        "REVIEW.md template missing the Findings table header row starting `| ID |`"
    )
    columns = [c.strip() for c in header.strip("|").split("|")]
    assert "Status" in columns, (
        "REVIEW.md Findings table must have a Status column "
        "(open|resolved|accepted-risk) — required by §5.3.9 ship gate"
    )


def test_review_template_status_values_documented() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    for value in ("open", "resolved", "accepted-risk"):
        assert value in text, f"Status value {value!r} must be documented in template"

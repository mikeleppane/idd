"""Tests for ``tools.validate.domain_glossary``.

The validator cross-references DOMAIN.md glossary rows against domain-flavoured
terms used in SPEC.md ``# Intent`` and ``# Scenarios`` and emits findings for
orphans, duplicates, unused entries, undefined-context annotations, and
malformed rows. It is a pure function — no subprocess, no I/O beyond reading
files.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import validate
from tools.validate.domain_glossary import validate_domain_glossary

_FEATURE_ID = "2026-05-09-glossary-fixture"


def _make_feature(tmp_path: Path, *, tier: str = "full") -> Path:
    feature_dir = tmp_path / ".forge" / "features" / _FEATURE_ID
    feature_dir.mkdir(parents=True)
    state = {
        "feature_id": _FEATURE_ID,
        "tier": tier,
        "current_phase": "domain",
        "phases": {},
        "skipped": [],
        "deviations": [],
        "commits": [],
    }
    (feature_dir / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    return feature_dir


def _write_spec(feature_dir: Path, intent: str, scenarios: str) -> None:
    body = f"---\nid: {_FEATURE_ID}\n---\n\n# Intent\n\n{intent}\n\n# Scenarios\n\n{scenarios}\n"
    (feature_dir / "SPEC.md").write_text(body, encoding="utf-8")


def _write_domain(
    feature_dir: Path,
    glossary_rows: list[str],
    *,
    status: str = "locked",
) -> None:
    rows = "\n".join(glossary_rows)
    body = (
        "---\n"
        f"id: {_FEATURE_ID}\n"
        f"status: {status}\n"
        "version: 0.1.0\n"
        "---\n\n"
        "# Glossary\n\n"
        "| Term | Definition | Context | Invariants |\n"
        "|---|---|---|---|\n"
        f"{rows}\n"
    )
    (feature_dir / "DOMAIN.md").write_text(body, encoding="utf-8")


def test_domain_glossary_clean_passes(tmp_path: Path) -> None:
    feature_dir = _make_feature(tmp_path)
    _write_spec(
        feature_dir,
        "The `Order` aggregate guards `Customer` purchases against `Cart` drift.",
        "Scenario: place order\n  Given a `Customer`\n  When `Order` is placed\n",
    )
    _write_domain(
        feature_dir,
        [
            "| [Order](context: sales) | The order aggregate. | sales | totals must reconcile |",
            "| [Customer](context: sales) | The buying party. | sales | identity is stable |",
            "| Cart | A pre-order shopping container. | sales | items belong to one Customer |",
        ],
    )

    findings = validate_domain_glossary(tmp_path, _FEATURE_ID)

    assert findings == []


def test_domain_glossary_orphan_term_blocks(tmp_path: Path) -> None:
    feature_dir = _make_feature(tmp_path)
    _write_spec(
        feature_dir,
        "The `Customer` purchases an `Order`.",
        "Scenario: stub\n  Given a `Customer`\n  When `Order` is placed\n",
    )
    _write_domain(
        feature_dir,
        ["| Order | An order. | — | — |"],
    )

    findings = validate_domain_glossary(tmp_path, _FEATURE_ID)

    orphan = [f for f in findings if "orphan_term" in f.message]
    assert orphan, findings
    assert orphan[0].severity == "BLOCK"
    assert "Customer" in orphan[0].message


def test_domain_glossary_duplicate_term_blocks(tmp_path: Path) -> None:
    feature_dir = _make_feature(tmp_path)
    _write_spec(
        feature_dir,
        "The `Order` aggregate.",
        "Scenario: stub\n  Given an `Order`\n  When placed\n",
    )
    _write_domain(
        feature_dir,
        [
            "| Order | An order. | — | — |",
            "| Order | An order, again. | — | — |",
        ],
    )

    findings = validate_domain_glossary(tmp_path, _FEATURE_ID)

    dups = [f for f in findings if "duplicate_term" in f.message]
    assert dups, findings
    assert dups[0].severity == "BLOCK"


def test_domain_glossary_unused_entry_medium(tmp_path: Path) -> None:
    feature_dir = _make_feature(tmp_path)
    _write_spec(
        feature_dir,
        "The `Order` aggregate.",
        "Scenario: stub\n  Given an `Order`\n  When placed\n",
    )
    _write_domain(
        feature_dir,
        [
            "| Order | An order. | — | — |",
            "| Cart | A pre-order shopping container. | — | — |",
        ],
    )

    findings = validate_domain_glossary(tmp_path, _FEATURE_ID)

    unused = [f for f in findings if "unused_glossary_entry" in f.message]
    assert unused, findings
    assert unused[0].severity == "MEDIUM"
    assert "Cart" in unused[0].message


def test_domain_glossary_undefined_context_low(tmp_path: Path) -> None:
    feature_dir = _make_feature(tmp_path)
    _write_spec(
        feature_dir,
        "The `Order` aggregate.",
        "Scenario: stub\n  Given an `Order`\n  When placed\n",
    )
    _write_domain(
        feature_dir,
        [
            "| [Order](context: billing) | An order. | sales | — |",
            "| Customer | A buyer. | sales | — |",
        ],
    )

    findings = validate_domain_glossary(tmp_path, _FEATURE_ID)

    undef = [f for f in findings if "undefined_context" in f.message]
    assert undef, findings
    assert undef[0].severity == "LOW"
    assert "billing" in undef[0].message


def test_domain_glossary_focused_tier_no_domain_md_returns_empty(tmp_path: Path) -> None:
    feature_dir = _make_feature(tmp_path, tier="focused")
    _write_spec(
        feature_dir,
        "The `Order` aggregate.",
        "Scenario: stub\n  Given an `Order`\n  When placed\n",
    )

    findings = validate_domain_glossary(tmp_path, _FEATURE_ID)

    assert findings == []


def test_domain_glossary_full_tier_missing_domain_md_blocks(tmp_path: Path) -> None:
    feature_dir = _make_feature(tmp_path, tier="full")
    _write_spec(
        feature_dir,
        "The `Order` aggregate.",
        "Scenario: stub\n  Given an `Order`\n  When placed\n",
    )

    findings = validate_domain_glossary(tmp_path, _FEATURE_ID)

    missing = [f for f in findings if "domain_md_missing" in f.message]
    assert missing, findings
    assert missing[0].severity == "BLOCK"


def test_domain_glossary_feature_missing_blocks(tmp_path: Path) -> None:
    findings = validate_domain_glossary(tmp_path, "does-not-exist")

    assert len(findings) == 1
    assert findings[0].severity == "BLOCK"
    assert "feature_missing" in findings[0].message


def test_domain_glossary_malformed_row_blocks(tmp_path: Path) -> None:
    feature_dir = _make_feature(tmp_path)
    _write_spec(
        feature_dir,
        "The `Order` aggregate.",
        "Scenario: stub\n  Given an `Order`\n  When placed\n",
    )
    # Malformed: only 2 cells (term + definition), missing context + invariants.
    _write_domain(
        feature_dir,
        ["| Order | An order. |"],
    )

    findings = validate_domain_glossary(tmp_path, _FEATURE_ID)

    malformed = [f for f in findings if "malformed_glossary_row" in f.message]
    assert malformed, findings
    assert malformed[0].severity == "BLOCK"


def test_domain_glossary_draft_status_downgrades_orphan_to_medium(tmp_path: Path) -> None:
    """Per locked plan P1.1: draft/ready emit advisory findings only."""
    feature_dir = _make_feature(tmp_path)
    _write_spec(
        feature_dir,
        "The `Customer` purchases an `Order`.",
        "Scenario: stub\n  Given a `Customer`\n  When `Order` is placed\n",
    )
    _write_domain(
        feature_dir,
        ["| Order | An order. | — | — |"],
        status="draft",
    )

    findings = validate_domain_glossary(tmp_path, _FEATURE_ID)

    orphans = [f for f in findings if "orphan_term" in f.message]
    assert orphans, findings
    # BLOCK demoted to MEDIUM while the author is still drafting.
    assert all(f.severity == "MEDIUM" for f in orphans)


def test_domain_glossary_locked_status_keeps_orphan_block(tmp_path: Path) -> None:
    feature_dir = _make_feature(tmp_path)
    _write_spec(
        feature_dir,
        "The `Customer` purchases an `Order`.",
        "Scenario: stub\n  Given a `Customer`\n  When `Order` is placed\n",
    )
    _write_domain(
        feature_dir,
        ["| Order | An order. | — | — |"],
        status="locked",
    )

    findings = validate_domain_glossary(tmp_path, _FEATURE_ID)

    orphans = [f for f in findings if "orphan_term" in f.message]
    assert orphans, findings
    assert all(f.severity == "BLOCK" for f in orphans)


def test_domain_glossary_terms_inside_fenced_block_ignored(tmp_path: Path) -> None:
    feature_dir = _make_feature(tmp_path)
    intent = (
        "The `Customer` purchases items.\n\n"
        "```\n"
        "An example using `Order` inside a code fence.\n"
        "```\n"
    )
    _write_spec(
        feature_dir,
        intent,
        "Scenario: stub\n  Given a `Customer`\n  When checkout\n",
    )
    _write_domain(
        feature_dir,
        ["| Customer | A buyer. | — | — |"],
    )

    findings = validate_domain_glossary(tmp_path, _FEATURE_ID)

    orphan = [f for f in findings if "orphan_term" in f.message]
    assert orphan == [], findings


def test_domain_glossary_cli_target_registered(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    feature_dir = _make_feature(tmp_path)
    _write_spec(
        feature_dir,
        "The `Customer` purchases an `Order`.",
        "Scenario: stub\n  Given a `Customer`\n  When `Order` placed\n",
    )
    _write_domain(
        feature_dir,
        ["| Order | An order. | — | — |"],
    )

    rc = validate.main(
        [
            "--target",
            "domain_glossary",
            "--repo-root",
            str(tmp_path),
            str(feature_dir),
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["target"] == "domain_glossary"
    assert any("orphan_term" in f["message"] for f in payload["findings"])
    assert rc == 1


def test_domain_glossary_cli_target_all_includes_glossary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    feature_dir = _make_feature(tmp_path)
    _write_spec(
        feature_dir,
        "The `Customer` purchases.",
        "Scenario: stub\n  Given a `Customer`\n  When checkout\n",
    )
    _write_domain(
        feature_dir,
        ["| Order | An order. | — | — |"],
    )

    rc = validate.main(["--target", "all", "--repo-root", str(tmp_path)])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["target"] == "all"
    assert any(
        f["target"] == "domain_glossary" and "orphan_term" in f["message"]
        for f in payload["findings"]
    ), payload
    assert rc == 1

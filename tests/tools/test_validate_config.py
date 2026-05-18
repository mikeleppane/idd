"""Tests for validate_config (.forge/config.json shape)."""

from __future__ import annotations

import json
from pathlib import Path

from tools.validate import Finding
from tools.validate._config_shape import validate_config


def test_missing_config_returns_no_findings(tmp_path: Path) -> None:
    findings = validate_config(tmp_path / "config.json")
    assert findings == []


def test_forward_schema_version_in_subblock_blocks(tmp_path: Path) -> None:
    """A subblock declaring schema_version > registry baseline BLOCKS."""
    config = tmp_path / "config.json"
    payload = {"cross_ai": {"schema_version": 9, "mode": "manual"}}
    config.write_text(json.dumps(payload), encoding="utf-8")

    findings = validate_config(config)

    assert any(
        f.severity == "BLOCK" and "schema_version 9 is newer" in f.message for f in findings
    ), findings


def test_happy_cross_ai_and_research_blocks(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    payload = {
        "cross_ai": {
            "mode": "manual",
            "timeout_seconds": 120,
        },
        "research": {
            "websearch_fallback": False,
            "byod_stale_days": 90,
        },
    }
    config.write_text(json.dumps(payload), encoding="utf-8")

    findings = validate_config(config)
    assert findings == [], findings


def test_happy_auto_mode_with_allowed_clis(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    payload = {
        "cross_ai": {
            "mode": "auto",
            "allowed_clis": ["codex"],
        },
    }
    config.write_text(json.dumps(payload), encoding="utf-8")

    findings = validate_config(config)
    assert findings == [], findings


def test_auto_mode_without_allowed_clis_blocks(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    payload = {"cross_ai": {"mode": "auto"}}
    config.write_text(json.dumps(payload), encoding="utf-8")

    findings = validate_config(config)
    assert any(f.severity == "BLOCK" for f in findings), findings
    assert any("allowed_clis" in f.message for f in findings), findings


def test_negative_byod_stale_days_blocks(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    payload = {"research": {"byod_stale_days": -1}}
    config.write_text(json.dumps(payload), encoding="utf-8")

    findings = validate_config(config)
    assert any(f.severity == "BLOCK" and "byod_stale_days" in f.message for f in findings), findings


def test_malformed_json_blocks(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text("{not valid json", encoding="utf-8")

    findings = validate_config(config)
    assert any(f.severity == "BLOCK" and "json" in f.message.lower() for f in findings), findings


def test_returns_list_of_findings(tmp_path: Path) -> None:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"cross_ai": {"mode": "manual"}}), encoding="utf-8")

    findings = validate_config(config)
    assert isinstance(findings, list)
    for finding in findings:
        assert isinstance(finding, Finding)


def test_top_level_non_mapping_blocks(tmp_path: Path) -> None:
    """A JSON array at the root is not a config object — must BLOCK."""
    config = tmp_path / "config.json"
    config.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    findings = validate_config(config)
    assert any(f.severity == "BLOCK" for f in findings), findings

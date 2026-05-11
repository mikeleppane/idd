"""Smoke: end-to-end coverage of the five grounding modes.

For each mode, exercise both the resolver
(:func:`tools.research.grounding.resolve_mode`) and — where a populated
fixture artifact exists — the artifact validator
(:func:`tools.validate._research_shape.validate_research`).

The fixture set under ``tests/fixtures/_research/`` covers the three
modes that produce a checked-in RESEARCH.md (``full``, ``degraded``,
``byod``); the ``websearch`` and ``byod-partial`` rows verify resolver
behaviour only because no canonical RESEARCH.md fixture is staged for
those modes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.research import grounding
from tools.validate import _research_shape

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "_research"
_PROBE_PATH = _FIXTURES / "fake_probe.json"

_BYOD_DIR = _FIXTURES / "research-byod" / ".forge" / "external-docs"

_RESEARCH_FULL = (
    _FIXTURES / "research-full" / ".forge" / "features" / "2026-05-11-sample" / "RESEARCH.md"
)
_RESEARCH_DEGRADED = (
    _FIXTURES / "research-degraded" / ".forge" / "features" / "2026-05-11-sample" / "RESEARCH.md"
)
_RESEARCH_BYOD = (
    _FIXTURES / "research-byod" / ".forge" / "features" / "2026-05-11-sample" / "RESEARCH.md"
)


def _probe(key: str) -> dict[str, object]:
    payload = json.loads(_PROBE_PATH.read_text(encoding="utf-8"))
    selected = payload[key]
    assert isinstance(selected, dict)
    return selected


@pytest.mark.parametrize(
    ("probe_key", "config", "libraries", "byod_dir", "expected_mode", "validator_fixture"),
    [
        ("full", {}, ("httpx", "pytest"), None, "full", _RESEARCH_FULL),
        ("neither", {}, ("httpx",), None, "degraded", _RESEARCH_DEGRADED),
        (
            "no_context7_yes_websearch",
            {"websearch_fallback": True},
            ("httpx",),
            None,
            "websearch",
            None,
        ),
        ("neither", {}, ("httpx",), _BYOD_DIR, "byod", _RESEARCH_BYOD),
        ("neither", {}, ("httpx", "pytest"), _BYOD_DIR, "byod-partial", None),
    ],
    ids=["full", "degraded", "websearch", "byod", "byod-partial"],
)
def test_grounding_modes_resolve_and_validate(
    probe_key: str,
    config: dict[str, object],
    libraries: tuple[str, ...],
    byod_dir: Path | None,
    expected_mode: str,
    validator_fixture: Path | None,
) -> None:
    probe = _probe(probe_key)

    resolved = grounding.resolve_mode(
        probe,
        config,
        libraries,
        byod_dir,
    )
    assert resolved == expected_mode

    if validator_fixture is not None:
        findings = _research_shape.validate_research(validator_fixture)
        assert findings == [], (
            f"expected empty findings for {expected_mode} fixture, got {findings!r}"
        )

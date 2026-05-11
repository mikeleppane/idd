"""Tests for the read-only ``get_phase_list`` accessor on ``tools.state``.

The accessor returns the canonical ordered phase list for a feature without
mutating the payload and without touching disk. It prefers an explicit
``routing.phase_list`` when present and otherwise lazily derives the list
from the feature's tier (and ``flow_version`` for the full tier).
"""

from __future__ import annotations

from typing import Any

import pytest

from tools import state
from tools.state import VALID_LIFECYCLE_PHASES, get_phase_list

_STANDARD_DEFAULT = [
    "spec",
    "scenarios",
    "plan",
    "crucible",
    "review",
    "execute",
    "verify",
    "ship",
]

_FULL_V3 = [
    "refine",
    "research",
    "spec",
    "domain",
    "scenarios",
    "plan",
    "crucible",
    "review",
    "execute",
    "verify",
    "ship",
    "qa",
]

_FULL_PRE_V3 = _FULL_V3[:-1]


def _routing(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    block: dict[str, Any] = {"final_tier": "focused"}
    if extra:
        block.update(extra)
    return block


def test_focused_legacy_state_derives_focused_list() -> None:
    payload: dict[str, Any] = {"tier": "focused", "routing": _routing()}

    result = get_phase_list(payload)

    assert result == ["spec", "execute", "verify"]


def test_standard_legacy_state_derives_no_research_standard_list() -> None:
    payload: dict[str, Any] = {"tier": "standard", "routing": _routing()}

    result = get_phase_list(payload)

    assert result == _STANDARD_DEFAULT


def test_full_legacy_v3_derives_twelve_element_list_with_qa() -> None:
    payload: dict[str, Any] = {
        "tier": "full",
        "flow_version": 3,
        "routing": _routing(),
    }

    result = get_phase_list(payload)

    assert result == _FULL_V3
    assert result is not None
    assert len(result) == 12
    assert result[-1] == "qa"


def test_full_legacy_v2_derives_eleven_element_list_without_qa() -> None:
    payload: dict[str, Any] = {
        "tier": "full",
        "flow_version": 2,
        "routing": _routing(),
    }

    result = get_phase_list(payload)

    assert result == _FULL_PRE_V3
    assert result is not None
    assert len(result) == 11
    assert "qa" not in result


def test_full_legacy_without_flow_version_treats_as_v1_and_omits_qa() -> None:
    payload: dict[str, Any] = {"tier": "full", "routing": _routing()}

    result = get_phase_list(payload)

    assert result == _FULL_PRE_V3
    assert result is not None
    assert "qa" not in result


def test_explicit_phase_list_in_state_wins_over_derivation() -> None:
    explicit = ["spec", "execute", "verify"]
    payload: dict[str, Any] = {
        # Tier says full, but explicit list wins even if "wrong" for the tier.
        "tier": "full",
        "flow_version": 3,
        "routing": _routing({"phase_list": explicit}),
    }

    result = get_phase_list(payload)

    assert result == explicit


def test_state_without_routing_block_returns_none() -> None:
    payload: dict[str, Any] = {"tier": "standard"}

    assert get_phase_list(payload) is None


def test_state_with_routing_but_no_tier_returns_none() -> None:
    payload: dict[str, Any] = {"routing": _routing()}

    assert get_phase_list(payload) is None


def test_returned_list_is_a_fresh_copy_and_does_not_alias_payload() -> None:
    explicit = ["spec", "execute", "verify"]
    payload: dict[str, Any] = {
        "tier": "focused",
        "routing": _routing({"phase_list": explicit}),
    }

    result = get_phase_list(payload)

    assert result == explicit
    assert result is not None
    assert result is not explicit  # defensive copy
    result.append("ship")  # mutate the returned list
    # The original payload list must be unaffected.
    assert payload["routing"]["phase_list"] == ["spec", "execute", "verify"]
    # Re-deriving still yields the original list.
    assert get_phase_list(payload) == ["spec", "execute", "verify"]


def test_every_emitted_phase_is_in_valid_lifecycle_phases() -> None:
    matrix: list[dict[str, Any]] = [
        {"tier": "focused", "routing": _routing()},
        {"tier": "standard", "routing": _routing()},
        {"tier": "full", "flow_version": 3, "routing": _routing()},
        {"tier": "full", "flow_version": 2, "routing": _routing()},
        {"tier": "full", "routing": _routing()},
    ]
    valid = set(VALID_LIFECYCLE_PHASES)
    for payload in matrix:
        result = get_phase_list(payload)
        assert result is not None, payload
        assert set(result).issubset(valid), (payload, result)


def test_routing_present_with_unknown_tier_returns_none() -> None:
    payload: dict[str, Any] = {"tier": "weird", "routing": _routing()}

    assert get_phase_list(payload) is None


def test_routing_phase_list_empty_falls_back_to_derivation() -> None:
    payload: dict[str, Any] = {
        "tier": "focused",
        "routing": _routing({"phase_list": []}),
    }

    # Empty list is not "non-empty"; accessor must derive instead.
    assert get_phase_list(payload) == ["spec", "execute", "verify"]


def test_routing_not_a_dict_returns_none() -> None:
    payload: dict[str, Any] = {"tier": "focused", "routing": "nope"}

    assert get_phase_list(payload) is None


def test_derive_phase_list_unknown_tier_raises_state_error() -> None:
    with pytest.raises(state.StateError, match="unknown tier"):
        state.derive_phase_list(tier="bogus")

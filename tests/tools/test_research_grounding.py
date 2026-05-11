"""Tests for the grounding-mode resolver decision tree.

The resolver is a pure function over the subagent's `grounding_probe`
payload, the parsed forge config, the canonical library list, and the
optional BYOD directory. The tests enumerate every branch of the
decision tree (per spec §5.3.9) using the shared `fake_probe.json`
variants so the fake stays the single source of truth.
"""

import json
from pathlib import Path

import pytest

from tools.research.grounding import resolve_mode

_FAKE_PROBE = Path(__file__).parent.parent / "fixtures" / "_research" / "fake_probe.json"


def _probe(variant: str) -> dict[str, bool]:
    payload: dict[str, dict[str, bool]] = json.loads(_FAKE_PROBE.read_text(encoding="utf-8"))
    return payload[variant]


def test_context7_callable_returns_full_regardless_of_other_flags() -> None:
    mode = resolve_mode(
        probe=_probe("full"),
        config={"websearch_fallback": True},
        libraries_extracted=("httpx",),
        byod_dir=None,
    )
    assert mode == "full"


def test_context7_callable_returns_full_even_without_libraries() -> None:
    mode = resolve_mode(
        probe=_probe("full"),
        config={},
        libraries_extracted=(),
        byod_dir=None,
    )
    assert mode == "full"


def test_byod_full_coverage_returns_byod(tmp_path: Path) -> None:
    (tmp_path / "httpx.md").write_text("doc", encoding="utf-8")
    (tmp_path / "rich.md").write_text("doc", encoding="utf-8")
    mode = resolve_mode(
        probe=_probe("neither"),
        config={"websearch_fallback": True},
        libraries_extracted=("httpx", "rich"),
        byod_dir=tmp_path,
    )
    assert mode == "byod"


def test_byod_partial_coverage_returns_byod_partial(tmp_path: Path) -> None:
    (tmp_path / "httpx.md").write_text("doc", encoding="utf-8")
    mode = resolve_mode(
        probe=_probe("neither"),
        config={},
        libraries_extracted=("httpx", "rich"),
        byod_dir=tmp_path,
    )
    assert mode == "byod-partial"


def test_byod_dir_normalizes_filename_for_match(tmp_path: Path) -> None:
    # Hyphenated filename must collapse to underscored canonical form to
    # match a hyphenated library name.
    (tmp_path / "my-lib.md").write_text("doc", encoding="utf-8")
    mode = resolve_mode(
        probe=_probe("neither"),
        config={},
        libraries_extracted=("my_lib",),
        byod_dir=tmp_path,
    )
    assert mode == "byod"


def test_websearch_fallback_when_byod_empty_and_websearch_present(
    tmp_path: Path,
) -> None:
    mode = resolve_mode(
        probe=_probe("no_context7_yes_websearch"),
        config={"websearch_fallback": True},
        libraries_extracted=("httpx",),
        byod_dir=tmp_path,
    )
    assert mode == "websearch"


def test_websearch_fallback_when_byod_dir_none() -> None:
    mode = resolve_mode(
        probe=_probe("no_context7_yes_websearch"),
        config={"websearch_fallback": True},
        libraries_extracted=("httpx",),
        byod_dir=None,
    )
    assert mode == "websearch"


def test_degraded_when_no_websearch_fallback_configured() -> None:
    mode = resolve_mode(
        probe=_probe("no_context7_yes_websearch"),
        config={"websearch_fallback": False},
        libraries_extracted=("httpx",),
        byod_dir=None,
    )
    assert mode == "degraded"


def test_degraded_when_websearch_fallback_true_but_websearch_absent() -> None:
    mode = resolve_mode(
        probe=_probe("neither"),
        config={"websearch_fallback": True},
        libraries_extracted=("httpx",),
        byod_dir=None,
    )
    assert mode == "degraded"


def test_empty_libraries_with_byod_dir_present_falls_to_step_three(
    tmp_path: Path,
) -> None:
    # Even though the directory has files, the empty libs list means
    # zero libs are covered → fall through to step 3.
    (tmp_path / "stale.md").write_text("doc", encoding="utf-8")
    mode = resolve_mode(
        probe=_probe("no_context7_yes_websearch"),
        config={"websearch_fallback": True},
        libraries_extracted=(),
        byod_dir=tmp_path,
    )
    assert mode == "websearch"


def test_byod_dir_present_but_no_matches_falls_to_step_three(
    tmp_path: Path,
) -> None:
    (tmp_path / "unrelated.md").write_text("doc", encoding="utf-8")
    mode = resolve_mode(
        probe=_probe("neither"),
        config={"websearch_fallback": False},
        libraries_extracted=("httpx",),
        byod_dir=tmp_path,
    )
    assert mode == "degraded"


@pytest.mark.parametrize(
    ("websearch_fallback", "websearch_present", "expected"),
    [
        (True, True, "websearch"),
        (True, False, "degraded"),
        (False, True, "degraded"),
        (False, False, "degraded"),
    ],
)
def test_step_three_truth_table(
    websearch_fallback: bool, websearch_present: bool, expected: str
) -> None:
    probe = {"context7_callable": False, "websearch_present": websearch_present}
    mode = resolve_mode(
        probe=probe,
        config={"websearch_fallback": websearch_fallback},
        libraries_extracted=("httpx",),
        byod_dir=None,
    )
    assert mode == expected


def test_resolve_mode_never_raises_on_minimal_inputs() -> None:
    mode = resolve_mode(
        probe={},
        config={},
        libraries_extracted=(),
        byod_dir=None,
    )
    assert mode == "degraded"


def test_websearch_fallback_under_research_subblock_root_config_shape() -> None:
    """The documented ``.forge/config.json`` shape nests under ``research``.

    Skill prose passes the whole config dict; resolver must extract
    ``config["research"]["websearch_fallback"]`` rather than reading a
    flat top-level key (which would never fire in practice).
    """
    mode = resolve_mode(
        probe=_probe("no_context7_yes_websearch"),
        config={"research": {"websearch_fallback": True}},
        libraries_extracted=("httpx",),
        byod_dir=None,
    )
    assert mode == "websearch"


def test_websearch_fallback_research_subblock_false_degrades() -> None:
    """Nested false explicitly degrades — does not leak into truthy fallback."""
    mode = resolve_mode(
        probe=_probe("no_context7_yes_websearch"),
        config={"research": {"websearch_fallback": False}},
        libraries_extracted=("httpx",),
        byod_dir=None,
    )
    assert mode == "degraded"


def test_websearch_fallback_research_subblock_missing_degrades() -> None:
    """Empty research sub-block also degrades — no implicit enablement."""
    mode = resolve_mode(
        probe=_probe("no_context7_yes_websearch"),
        config={"research": {}},
        libraries_extracted=("httpx",),
        byod_dir=None,
    )
    assert mode == "degraded"

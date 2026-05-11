"""Tests for the mode-aware citation validator.

Each grounding mode (full / byod / websearch / degraded / byod-partial)
has a passing-body case and a failing-body case. Boundary cases cover
empty bodies and bodies with no code-fenced symbols (no citation
required). Lines inside fenced ``` blocks must be skipped so that the
detection-table example fragments do not trigger spurious "missing
citation" findings.
"""

import pytest

from tools.research.citations import CitationResult, validate

_DEGRADED_MARKER = "_Context7 not available — research ran in **degraded** mode._"


def test_validate_returns_citation_result_dataclass() -> None:
    result = validate("", mode="full")
    assert isinstance(result, CitationResult)
    assert result.missing_citations == []
    assert result.degraded_marker_present is False
    assert result.byod_partial_uncovered == []


def test_validate_empty_body_full_mode_passes() -> None:
    result = validate("", mode="full")
    assert result.missing_citations == []


def test_validate_no_code_symbols_means_no_citation_required() -> None:
    body = "This paragraph mentions no API symbols.\n\nNor does this one."
    result = validate(body, mode="full")
    assert result.missing_citations == []


def test_validate_full_mode_passes_with_context7_citation() -> None:
    body = "Use `HttpxClient` to fetch data. [context7:/encode/httpx:snip-1]"
    result = validate(body, mode="full")
    assert result.missing_citations == []


def test_validate_full_mode_blocks_when_citation_missing() -> None:
    body = "Use `HttpxClient` to fetch data."
    result = validate(body, mode="full")
    assert len(result.missing_citations) == 1
    assert "HttpxClient" in result.missing_citations[0]


def test_validate_full_mode_byod_citation_does_not_satisfy() -> None:
    body = "Call `Send.dispatch` to enqueue. [byod:httpx:overview]"
    result = validate(body, mode="full")
    assert len(result.missing_citations) == 1


def test_validate_byod_mode_passes_with_byod_citation() -> None:
    body = "Call `Client.send` for the request. [byod:httpx:client]"
    result = validate(body, mode="byod")
    assert result.missing_citations == []


def test_validate_byod_mode_blocks_when_citation_missing() -> None:
    body = "Call `Client.send` for the request."
    result = validate(body, mode="byod")
    assert len(result.missing_citations) == 1


def test_validate_websearch_mode_passes_with_websearch_citation() -> None:
    body = "Use `Foo` to bar. [websearch:https://example.com/api/foo]"
    result = validate(body, mode="websearch")
    assert result.missing_citations == []


def test_validate_websearch_mode_blocks_when_citation_missing() -> None:
    body = "Use `Foo` to bar."
    result = validate(body, mode="websearch")
    assert len(result.missing_citations) == 1


def test_validate_degraded_mode_passes_with_marker_present() -> None:
    body = f"# External docs\n\n{_DEGRADED_MARKER}\n\nDiscusses `SomeApi` without a real cite."
    result = validate(body, mode="degraded")
    assert result.missing_citations == []
    assert result.degraded_marker_present is True


def test_validate_degraded_mode_blocks_when_marker_missing() -> None:
    body = "Discusses `SomeApi` but never says context7 is unavailable."
    result = validate(body, mode="degraded")
    assert result.degraded_marker_present is False


def test_validate_byod_partial_passes_for_covered_libraries() -> None:
    body = (
        f"# External docs\n\n{_DEGRADED_MARKER}\n\n"
        "About httpx: call `Client.send`. [byod:httpx:client]\n\n"
        "About requests: call `Session.get`. [byod:requests:session]"
    )
    result = validate(
        body,
        mode="byod-partial",
        libraries=("httpx", "requests"),
    )
    assert result.missing_citations == []
    assert result.byod_partial_uncovered == []


def test_validate_byod_partial_flags_uncovered_libraries() -> None:
    # `pydantic` is mentioned (referenced in identifier) and lacks coverage,
    # while `httpx` is covered. The marker is present so degraded fallback
    # is satisfied for the uncovered slot.
    body = (
        f"# External docs\n\n{_DEGRADED_MARKER}\n\n"
        "About httpx: call `Client.send`. [byod:httpx:client]\n\n"
        "About pydantic: use `pydantic.BaseModel` for validation."
    )
    result = validate(
        body,
        mode="byod-partial",
        libraries=("httpx",),
    )
    assert "pydantic" in result.byod_partial_uncovered


def test_validate_skips_fenced_code_blocks() -> None:
    body = "```python\nuse `SomeApi` here\n```\n\nReal prose without symbols."
    result = validate(body, mode="full")
    assert result.missing_citations == []


@pytest.mark.parametrize(
    "mode",
    ["full", "byod", "websearch", "degraded", "byod-partial"],
)
def test_validate_accepts_all_documented_modes(mode: str) -> None:
    # Should never raise for any documented mode value.
    validate("", mode=mode, libraries=("httpx",))


def test_validate_degraded_marker_inside_html_comment_does_not_count() -> None:
    """An unmodified template ships the marker inside an HTML comment.

    The marker check must look at visible body only — a status=done,
    research_grounding=degraded artifact copied straight from the
    template would otherwise pass without the subagent ever replacing
    the External docs section.
    """
    body = (
        "# External docs\n\n"
        "<!--\n"
        "_Context7 not available — research ran in **degraded** mode._\n"
        "-->\n\n"
        "Some other prose."
    )
    result = validate(body, mode="degraded")
    assert result.degraded_marker_present is False


def test_validate_degraded_marker_in_visible_body_counts() -> None:
    """Marker present in visible body satisfies the degraded rule."""
    body = (
        "# External docs\n\n"
        "_Context7 not available — research ran in **degraded** mode._\n\n"
        "Discusses `SomeApi` without authoritative cite."
    )
    result = validate(body, mode="degraded")
    assert result.degraded_marker_present is True

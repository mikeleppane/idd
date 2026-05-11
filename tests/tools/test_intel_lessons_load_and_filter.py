"""Tests for tools.intel.lessons.load_and_filter dispatch-budget loader.

The loader is the consumer-facing surface ``commands/forge:dispatch`` will
call to splice the lessons list into the budget JSON alongside the
Constitution articles. These tests pin its filter semantics: status filter
silently drops retired / superseded rows, the percentile gate uses the
shared helper's ``<`` comparison, and the ``MAX_LESSON_WORDS`` cap is
independent of the Constitution cap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.intel import lessons

_HEADER = '---\nversion: 0.1.0\ncreated: "2026-05-11"\n---\n\n# FORGE Lessons\n\n'


def _entry(
    *,
    nid: str = "L001",
    title: str = "Example trap",
    captured: str = "2026-05-11",
    feature: str = "m0-example",
    resolved_by: str = "manual",
    trap: str = "Subagent did the wrong thing.",
    avoidance: str = "Subagent should do the right thing.",
    tags: str = "dispatch, validation",
    severity: str = "LOW",
    status: str = "active",
) -> str:
    return (
        f"## {nid} — {title}\n"
        f"**Captured:** {captured} from feature {feature}\n"
        f"**Resolved by:** {resolved_by}\n"
        f"**Trap:** {trap}\n"
        f"**Avoidance:** {avoidance}\n"
        f"**Tags:** {tags}\n"
        f"**Severity:** {severity}\n"
        f"**Status:** {status}\n"
    )


def _file(*entries: str) -> str:
    return _HEADER + "\n".join(entries)


def _write_lessons(repo_root: Path, body: str) -> Path:
    path = repo_root / ".forge" / "intel" / "lessons.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_load_and_filter_returns_empty_pair_when_file_missing(tmp_path: Path) -> None:
    kept, dropped = lessons.load_and_filter(tmp_path, idea_text="anything")
    assert kept == []
    assert dropped == []


def test_load_and_filter_keeps_critical_with_no_scope_match(tmp_path: Path) -> None:
    body = _file(_entry(nid="L001", severity="CRITICAL", tags="dispatch"))
    _write_lessons(tmp_path, body)
    kept, dropped = lessons.load_and_filter(tmp_path, idea_text="unrelated topic")
    assert [le.id for le in kept] == ["L001"]
    assert dropped == []


def test_load_and_filter_drops_retired_lessons_silently(tmp_path: Path) -> None:
    body = _file(
        _entry(nid="L001", severity="CRITICAL", tags="dispatch"),
        _entry(nid="L002", severity="CRITICAL", tags="dispatch", status="retired"),
    )
    _write_lessons(tmp_path, body)
    kept, dropped = lessons.load_and_filter(tmp_path)
    kept_ids = [le.id for le in kept]
    assert "L002" not in kept_ids
    # Status-dropped ids are silent — they do not pollute the relevance
    # dropped list.
    assert "L002" not in dropped


def test_load_and_filter_drops_superseded_lessons_silently(tmp_path: Path) -> None:
    body = _file(
        _entry(nid="L001", severity="CRITICAL", tags="dispatch", status="superseded-by:L002"),
        _entry(nid="L002", severity="CRITICAL", tags="dispatch"),
    )
    _write_lessons(tmp_path, body)
    kept, dropped = lessons.load_and_filter(tmp_path)
    kept_ids = [le.id for le in kept]
    assert kept_ids == ["L002"]
    assert "L001" not in dropped


def test_load_and_filter_returns_empty_pair_when_all_retired(tmp_path: Path) -> None:
    body = _file(_entry(nid="L001", severity="CRITICAL", status="retired"))
    _write_lessons(tmp_path, body)
    kept, dropped = lessons.load_and_filter(tmp_path)
    assert kept == []
    assert dropped == []


def test_load_and_filter_high_with_no_score_kept_when_all_scores_zero(
    tmp_path: Path,
) -> None:
    """When all active lessons score 0 (no scope match), p25 == 0; the
    strict ``<`` percentile gate keeps everything because score < 0 is
    never true. Pins the behavior the shared helper inherits from the
    Constitution filter's inequality choice.
    """
    body = _file(
        _entry(nid="L001", severity="HIGH", tags="dispatch"),
        _entry(nid="L002", severity="HIGH", tags="validation"),
    )
    _write_lessons(tmp_path, body)
    kept, dropped = lessons.load_and_filter(tmp_path, idea_text="")
    assert {le.id for le in kept} == {"L001", "L002"}
    assert dropped == []


def test_load_and_filter_scope_keywords_score_via_tag_intersection(tmp_path: Path) -> None:
    """A scope keyword that matches one lesson's tag should pull it above
    the percentile threshold over a peer with no overlap.
    """
    body = _file(
        # Three lessons match the scope keywords, three do not. Scores end
        # up [1,0,0,1,1,0]; sorted [0,0,0,1,1,1] -> median 0.5 and the
        # strict "< 0.5" gate drops the three zeros.
        _entry(nid="L001", severity="MEDIUM", tags="dispatch"),  # score 1
        _entry(nid="L002", severity="MEDIUM", tags="validation"),  # score 0
        _entry(nid="L003", severity="MEDIUM", tags="async"),  # score 0
        _entry(nid="L004", severity="MEDIUM", tags="fixtures"),  # score 1
        _entry(nid="L005", severity="MEDIUM", tags="secrets"),  # score 0
        _entry(nid="L006", severity="MEDIUM", tags="bdd"),  # score 1
    )
    _write_lessons(tmp_path, body)
    kept, dropped = lessons.load_and_filter(tmp_path, idea_text="dispatch fixtures bdd")
    kept_ids = {le.id for le in kept}
    # "dispatch" matches L001's tag, "fixtures" matches L004's tag, "bdd"
    # matches L006's tag — those three rise above the median.
    assert {"L001", "L004", "L006"} <= kept_ids
    # Lessons with no overlap (L002, L003, L005) score 0 and fall below the
    # 0.5 median, so they drop.
    assert "L002" in dropped
    assert "L003" in dropped
    assert "L005" in dropped


def test_load_and_filter_caps_by_ascending_score_when_over_cap(tmp_path: Path) -> None:
    long_trap = "trap " + ("word " * 200)
    long_avoidance = "avoidance " + ("word " * 200)
    body = _file(
        _entry(nid="L001", severity="HIGH", trap=long_trap, avoidance=long_avoidance),
        _entry(nid="L002", severity="HIGH", trap=long_trap, avoidance=long_avoidance),
        _entry(nid="L003", severity="HIGH", trap=long_trap, avoidance=long_avoidance),
    )
    _write_lessons(tmp_path, body)
    kept, dropped = lessons.load_and_filter(tmp_path)
    total_words = sum(le.body_words for le in kept)
    assert total_words <= lessons.MAX_LESSON_WORDS
    assert dropped, "cap pressure must force >= 1 drop"


def test_load_and_filter_raises_when_critical_alone_exceed_cap(tmp_path: Path) -> None:
    bloat = "word " * 400  # ~400 words per field
    body = _file(
        _entry(nid="L001", severity="CRITICAL", trap=bloat, avoidance=bloat),
        _entry(nid="L002", severity="CRITICAL", trap=bloat, avoidance=bloat),
    )
    _write_lessons(tmp_path, body)
    with pytest.raises(lessons.LessonError, match=r"CRITICAL lessons .* exceed"):
        lessons.load_and_filter(tmp_path)


def test_load_and_filter_is_deterministic_across_repeated_calls(tmp_path: Path) -> None:
    body = _file(
        _entry(nid="L001", severity="CRITICAL", tags="dispatch"),
        _entry(nid="L002", severity="MEDIUM", tags="validation"),
        _entry(nid="L003", severity="HIGH", tags="async"),
    )
    _write_lessons(tmp_path, body)
    first_kept, first_dropped = lessons.load_and_filter(tmp_path, idea_text="dispatch")
    second_kept, second_dropped = lessons.load_and_filter(tmp_path, idea_text="dispatch")
    assert [le.id for le in first_kept] == [le.id for le in second_kept]
    assert first_dropped == second_dropped


def test_load_and_filter_uses_files_in_scope_for_keywords(tmp_path: Path) -> None:
    # Three of the five lessons share tags that the scope paths tokenize to,
    # so scores [1,0,1,0,1] sort to [0,0,1,1,1] -> median 1 and the two
    # zero-scoring lessons fall below.
    body = _file(
        _entry(nid="L001", severity="MEDIUM", tags="dispatch"),
        _entry(nid="L002", severity="MEDIUM", tags="async"),
        _entry(nid="L003", severity="MEDIUM", tags="fixtures"),
        _entry(nid="L004", severity="MEDIUM", tags="secrets"),
        _entry(nid="L005", severity="MEDIUM", tags="validation"),
    )
    _write_lessons(tmp_path, body)
    kept, dropped = lessons.load_and_filter(
        tmp_path,
        files_in_scope=[
            Path("src/dispatch/router.py"),
            Path("tests/fixtures/dispatch_payload.json"),
            Path("tools/validation/preflight.py"),
        ],
    )
    kept_ids = {le.id for le in kept}
    # Path tokens include "dispatch", "fixtures", "validation"; those three
    # lessons score 1 each, the others stay at 0 and drop below the median.
    assert {"L001", "L003", "L005"} <= kept_ids
    assert "L002" in dropped
    assert "L004" in dropped


def test_load_and_filter_kept_sorted_by_numeric_id(tmp_path: Path) -> None:
    # Parser already requires monotonic id order, so the loader inherits
    # that ordering naturally. Pin numeric (not lexicographic) sort by
    # spanning past L009 where string sort would re-order L010 before L002.
    body = _file(
        _entry(nid="L001", severity="CRITICAL", tags="dispatch"),
        _entry(nid="L002", severity="CRITICAL", tags="dispatch"),
        _entry(nid="L010", severity="CRITICAL", tags="dispatch"),
    )
    _write_lessons(tmp_path, body)
    kept, _dropped = lessons.load_and_filter(tmp_path)
    assert [le.id for le in kept] == ["L001", "L002", "L010"]


def test_load_and_filter_exposes_max_lesson_words_constant() -> None:
    assert lessons.MAX_LESSON_WORDS == 600

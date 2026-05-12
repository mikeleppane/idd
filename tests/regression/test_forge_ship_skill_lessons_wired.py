"""Static contract tests pinning lesson wiring in the forge-ship skill.

The ship gate parses both ``[constitution:A<n>]`` and ``[lesson:L<NNN>]``
tags from REVIEW.code.md, but earlier revisions of the skill only called
``partition_by_article_level``. Lesson-kind findings then fell through to
the ``info`` bucket (because their ``article_id`` is ``None``) and were
silently ignored at ship time. These tests pin the lesson-aware call
shape so the wiring cannot regress.
"""

from __future__ import annotations

from pathlib import Path

SKILL = Path("skills/forge-ship/SKILL.md")


def _skill_body() -> str:
    return SKILL.read_text(encoding="utf-8")


def test_skill_loads_filtered_lessons_before_partition() -> None:
    body = _skill_body()
    assert "tools.intel.lessons.load_and_filter(repo_root)" in body, (
        "Skill must call tools.intel.lessons.load_and_filter so the lesson "
        "partition step has the active+relevant lessons in hand"
    )


def test_skill_partitions_by_lesson_severity() -> None:
    body = _skill_body()
    assert "tools.ship_gate.partition_by_lesson_severity(findings, lessons)" in body, (
        "Skill must call partition_by_lesson_severity on the parsed findings "
        "so lesson-kind findings route to the right bucket"
    )


def test_skill_merges_article_and_lesson_gate_buckets() -> None:
    body = _skill_body()
    assert "gate = gate_a + gate_l" in body, (
        "Skill must merge the article and lesson gate buckets so a "
        "lesson-CRITICAL or lesson-HIGH finding blocks ship just like a "
        "CRITICAL article"
    )
    assert "warn = warn_a + warn_l" in body, (
        "Skill must merge the article and lesson warn buckets so MEDIUM-"
        "severity lesson findings render alongside SHOULD-article advisories"
    )


def test_skill_passes_lessons_keyword_into_render_calls() -> None:
    body = _skill_body()
    assert "render_warn_summary(warn, articles, lessons=lessons)" in body, (
        "render_warn_summary must receive lessons=lessons so lesson-kind "
        "findings render with their Trap/Avoidance fragments"
    )
    assert "render_gate_prompt(gate, articles, lessons=lessons)" in body, (
        "render_gate_prompt must receive lessons=lessons so lesson-kind "
        "findings render with their Trap/Avoidance fragments"
    )


def test_skill_passes_lessons_keyword_into_ack_hook() -> None:
    body = _skill_body()
    assert "lessons=lessons" in body and "make_acknowledgement_hook" in body, (
        "make_acknowledgement_hook call must include lessons=lessons so "
        "lesson-kind ACK entries get the right title fragment in decisions.md"
    )

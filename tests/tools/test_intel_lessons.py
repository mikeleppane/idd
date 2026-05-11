"""Tests for tools.intel.lessons parser, allocator, append, amend, template."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tools.intel import lessons

TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "intel" / "lessons.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _write(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Parser — happy path
# ---------------------------------------------------------------------------


def test_parse_template_yields_single_lesson() -> None:
    """The shipped template parses cleanly into the seed entry."""
    assert TEMPLATE_PATH.exists()
    parsed = lessons.parse(TEMPLATE_PATH)
    assert len(parsed) == 1
    assert parsed[0].id == "L001"


def test_parse_two_entries_in_declaration_order(tmp_path: Path) -> None:
    body = _file(_entry(nid="L001"), _entry(nid="L002", title="Second"))
    path = _write(tmp_path / ".forge" / "intel" / "lessons.md", body)
    parsed = lessons.parse(path)
    assert [e.id for e in parsed] == ["L001", "L002"]


def test_parse_tokenises_tags_with_whitespace(tmp_path: Path) -> None:
    body = _file(_entry(tags="dispatch , validation ,async"))
    path = _write(tmp_path / "lessons.md", body)
    parsed = lessons.parse(path)
    assert parsed[0].tags == ("dispatch", "validation", "async")


def test_parse_dedupes_tags(tmp_path: Path) -> None:
    body = _file(_entry(tags="dispatch, dispatch, validation"))
    path = _write(tmp_path / "lessons.md", body)
    parsed = lessons.parse(path)
    assert parsed[0].tags == ("dispatch", "validation")


def test_parse_missing_file_returns_empty_list(tmp_path: Path) -> None:
    assert lessons.parse(tmp_path / "nonexistent.md") == []


def test_parse_concatenates_multi_line_trap_and_avoidance(tmp_path: Path) -> None:
    body = _file(
        "## L001 — Multi-line\n"
        "**Captured:** 2026-05-11 from feature x\n"
        "**Resolved by:** manual\n"
        "**Trap:** First trap line.\n"
        "Second trap line continues.\n"
        "**Avoidance:** First avoidance line.\n"
        "Second avoidance line continues.\n"
        "**Tags:** dispatch\n"
        "**Severity:** LOW\n"
        "**Status:** active\n"
    )
    path = _write(tmp_path / "lessons.md", body)
    parsed = lessons.parse(path)
    assert "First trap line. Second trap line continues" in parsed[0].trap
    assert "First avoidance line. Second avoidance line continues" in parsed[0].avoidance


def test_parse_body_words_counts_trap_plus_avoidance(tmp_path: Path) -> None:
    body = _file(_entry(trap="one two three", avoidance="four five"))
    path = _write(tmp_path / "lessons.md", body)
    parsed = lessons.parse(path)
    assert parsed[0].body_words == 5


# ---------------------------------------------------------------------------
# Parser — shape failures
# ---------------------------------------------------------------------------


def test_parse_missing_required_field_raises(tmp_path: Path) -> None:
    bad_entry = (
        "## L001 — Missing trap\n"
        "**Captured:** 2026-05-11 from feature x\n"
        "**Resolved by:** manual\n"
        "**Avoidance:** Stub.\n"
        "**Tags:** dispatch\n"
        "**Severity:** LOW\n"
        "**Status:** active\n"
    )
    path = _write(tmp_path / "lessons.md", _file(bad_entry))
    with pytest.raises(lessons.LessonError, match="Trap"):
        lessons.parse(path)


def test_parse_rejects_tag_outside_vocabulary(tmp_path: Path) -> None:
    body = _file(_entry(tags="imports, made-up-tag"))
    path = _write(tmp_path / "lessons.md", body)
    with pytest.raises(lessons.LessonError, match="made-up-tag"):
        lessons.parse(path)


def test_parse_rejects_empty_tags_list(tmp_path: Path) -> None:
    body = _file(_entry(tags=""))
    path = _write(tmp_path / "lessons.md", body)
    with pytest.raises(lessons.LessonError, match=r"[Tt]ags"):
        lessons.parse(path)


def test_parse_rejects_bad_date(tmp_path: Path) -> None:
    body = _file(_entry(captured="not-a-date"))
    path = _write(tmp_path / "lessons.md", body)
    with pytest.raises(lessons.LessonError, match=r"[Cc]aptured"):
        lessons.parse(path)


def test_parse_rejects_missing_captured_from(tmp_path: Path) -> None:
    bad_entry = (
        "## L001 — No feature\n"
        "**Captured:** 2026-05-11\n"
        "**Resolved by:** manual\n"
        "**Trap:** x\n"
        "**Avoidance:** y\n"
        "**Tags:** dispatch\n"
        "**Severity:** LOW\n"
        "**Status:** active\n"
    )
    path = _write(tmp_path / "lessons.md", _file(bad_entry))
    with pytest.raises(lessons.LessonError, match=r"[Cc]aptured"):
        lessons.parse(path)


def test_parse_rejects_bad_resolved_by(tmp_path: Path) -> None:
    body = _file(_entry(resolved_by="halfway-sha"))
    path = _write(tmp_path / "lessons.md", body)
    with pytest.raises(lessons.LessonError, match=r"[Rr]esolved"):
        lessons.parse(path)


def test_parse_accepts_40_hex_resolved_by(tmp_path: Path) -> None:
    sha = "a" * 40
    body = _file(_entry(resolved_by=sha))
    path = _write(tmp_path / "lessons.md", body)
    parsed = lessons.parse(path)
    assert parsed[0].resolved_by == sha


def test_parse_rejects_bad_severity(tmp_path: Path) -> None:
    body = _file(_entry(severity="WARN"))
    path = _write(tmp_path / "lessons.md", body)
    with pytest.raises(lessons.LessonError, match=r"[Ss]everity"):
        lessons.parse(path)


def test_parse_rejects_bad_status(tmp_path: Path) -> None:
    body = _file(_entry(status="deprecated"))
    path = _write(tmp_path / "lessons.md", body)
    with pytest.raises(lessons.LessonError, match=r"[Ss]tatus"):
        lessons.parse(path)


def test_parse_rejects_bad_superseded_target_shape(tmp_path: Path) -> None:
    body = _file(_entry(status="superseded-by:L42"))
    path = _write(tmp_path / "lessons.md", body)
    with pytest.raises(lessons.LessonError, match="superseded"):
        lessons.parse(path)


def test_parse_allows_forward_superseded_reference(tmp_path: Path) -> None:
    """Supersession arrows point forward in time: a newer (higher-id) lesson
    replaces an older one. The parser must accept that direction.
    """
    body = _file(
        _entry(nid="L001", status="superseded-by:L005"),
        _entry(nid="L002"),
        _entry(nid="L005"),
    )
    path = _write(tmp_path / "lessons.md", body)
    parsed = lessons.parse(path)
    assert parsed[0].status == "superseded-by:L005"


def test_parse_rejects_chained_supersession(tmp_path: Path) -> None:
    body = _file(
        _entry(nid="L001", status="superseded-by:L002"),
        _entry(nid="L002", status="superseded-by:L003"),
        _entry(nid="L003"),
    )
    path = _write(tmp_path / "lessons.md", body)
    with pytest.raises(lessons.LessonError, match="chain"):
        lessons.parse(path)


def test_parse_rejects_superseded_target_missing(tmp_path: Path) -> None:
    body = _file(_entry(nid="L001", status="superseded-by:L999"))
    path = _write(tmp_path / "lessons.md", body)
    with pytest.raises(lessons.LessonError, match="L999"):
        lessons.parse(path)


def test_parse_rejects_malformed_id(tmp_path: Path) -> None:
    bad_entry = (
        "## L1 — Bad id\n"
        "**Captured:** 2026-05-11 from feature x\n"
        "**Resolved by:** manual\n"
        "**Trap:** x\n"
        "**Avoidance:** y\n"
        "**Tags:** dispatch\n"
        "**Severity:** LOW\n"
        "**Status:** active\n"
    )
    path = _write(tmp_path / "lessons.md", _file(bad_entry))
    # Header regex requires L\d{3}; the malformed header is not a lesson at all,
    # so the file parses to zero lessons. The parser still detects the row via
    # the `## L` prefix and surfaces the error.
    with pytest.raises(lessons.LessonError):
        lessons.parse(path)


# ---------------------------------------------------------------------------
# Parser — cross-entry
# ---------------------------------------------------------------------------


def test_parse_rejects_duplicate_ids(tmp_path: Path) -> None:
    body = _file(_entry(nid="L001"), _entry(nid="L001", title="Dup"))
    path = _write(tmp_path / "lessons.md", body)
    with pytest.raises(lessons.LessonError, match="L001"):
        lessons.parse(path)


def test_parse_rejects_non_monotonic_order(tmp_path: Path) -> None:
    body = _file(_entry(nid="L005"), _entry(nid="L003"))
    path = _write(tmp_path / "lessons.md", body)
    with pytest.raises(lessons.LessonError, match=r"monotonic|order"):
        lessons.parse(path)


def test_parse_allows_gaps(tmp_path: Path) -> None:
    body = _file(_entry(nid="L001"), _entry(nid="L003"))
    path = _write(tmp_path / "lessons.md", body)
    parsed = lessons.parse(path)
    assert [e.id for e in parsed] == ["L001", "L003"]


# ---------------------------------------------------------------------------
# Parser — code-region strip
# ---------------------------------------------------------------------------


def test_parse_ignores_lessons_inside_fenced_code(tmp_path: Path) -> None:
    body = (
        _HEADER
        + _entry(nid="L001")
        + "\n"
        + "Example of how to write one:\n\n"
        + "```markdown\n"
        + "## L099 — Phantom lesson\n"
        + "**Tags:** dispatch\n"
        + "```\n"
    )
    path = _write(tmp_path / "lessons.md", body)
    parsed = lessons.parse(path)
    assert [e.id for e in parsed] == ["L001"]


# ---------------------------------------------------------------------------
# parse_text in-memory counterpart
# ---------------------------------------------------------------------------


def test_parse_text_matches_parse_on_disk(tmp_path: Path) -> None:
    body = _file(_entry(nid="L001"), _entry(nid="L002"))
    path = _write(tmp_path / "lessons.md", body)
    on_disk = lessons.parse(path)
    in_memory = lessons.parse_text(body)
    assert [e.id for e in on_disk] == [e.id for e in in_memory]


# ---------------------------------------------------------------------------
# next_id
# ---------------------------------------------------------------------------


def test_next_id_missing_file_returns_l001(tmp_path: Path) -> None:
    assert lessons.next_id(tmp_path) == "L001"


def test_next_id_one_entry_returns_l002(tmp_path: Path) -> None:
    body = _file(_entry(nid="L001"))
    _write(tmp_path / ".forge" / "intel" / "lessons.md", body)
    assert lessons.next_id(tmp_path) == "L002"


def test_next_id_with_gaps_continues_past_max(tmp_path: Path) -> None:
    body = _file(_entry(nid="L001"), _entry(nid="L003"))
    _write(tmp_path / ".forge" / "intel" / "lessons.md", body)
    assert lessons.next_id(tmp_path) == "L004"


def test_next_id_malformed_file_raises(tmp_path: Path) -> None:
    body = _file(_entry(severity="WARN"))
    _write(tmp_path / ".forge" / "intel" / "lessons.md", body)
    with pytest.raises(lessons.LessonError):
        lessons.next_id(tmp_path)


# ---------------------------------------------------------------------------
# append
# ---------------------------------------------------------------------------


def _make_lesson(**overrides: object) -> lessons.Lesson:
    defaults: dict[str, object] = {
        "id": "L001",
        "captured": date(2026, 5, 11),
        "captured_from": "m0-example",
        "resolved_by": "manual",
        "trap": "Subagent did the wrong thing.",
        "avoidance": "Subagent should do the right thing.",
        "tags": ("dispatch", "validation"),
        "severity": "LOW",
        "status": "active",
        "body_words": 0,
    }
    defaults.update(overrides)
    return lessons.Lesson(**defaults)  # type: ignore[arg-type]


def test_append_creates_file_from_template(tmp_path: Path) -> None:
    draft = _make_lesson(id="L001")
    path = lessons.append(tmp_path, draft)
    assert path == tmp_path / ".forge" / "intel" / "lessons.md"
    parsed = lessons.parse(path)
    assert [e.id for e in parsed] == ["L001"]


def test_append_adds_to_existing_file(tmp_path: Path) -> None:
    seed = _make_lesson(id="L001")
    lessons.append(tmp_path, seed)
    second = _make_lesson(id="L002")
    lessons.append(tmp_path, second)
    parsed = lessons.parse(tmp_path / ".forge" / "intel" / "lessons.md")
    assert [e.id for e in parsed] == ["L001", "L002"]


def test_append_rejects_skipped_slot(tmp_path: Path) -> None:
    lessons.append(tmp_path, _make_lesson(id="L001"))
    bad = _make_lesson(id="L005")
    with pytest.raises(lessons.LessonError, match="L00"):
        lessons.append(tmp_path, bad)


def test_append_atomic_write_failure_preserves_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lessons.append(tmp_path, _make_lesson(id="L001"))
    path = tmp_path / ".forge" / "intel" / "lessons.md"
    before = path.read_text(encoding="utf-8")

    def boom(target: Path, body: str) -> None:
        del target, body
        raise OSError("simulated disk failure")

    monkeypatch.setattr(lessons, "atomic_replace", boom)
    with pytest.raises(OSError, match="simulated"):
        lessons.append(tmp_path, _make_lesson(id="L002"))
    assert path.read_text(encoding="utf-8") == before


def test_append_today_override(tmp_path: Path) -> None:
    """today override flows into the header `created:` line on file creation."""
    draft = _make_lesson(id="L001", captured=date(2026, 1, 1))
    lessons.append(tmp_path, draft, today=date(2026, 1, 1))
    text = (tmp_path / ".forge" / "intel" / "lessons.md").read_text(encoding="utf-8")
    assert "2026-01-01" in text


# ---------------------------------------------------------------------------
# amend_status
# ---------------------------------------------------------------------------


def test_amend_status_active_to_retired(tmp_path: Path) -> None:
    lessons.append(tmp_path, _make_lesson(id="L001"))
    lessons.amend_status(tmp_path, "L001", "retired")
    parsed = lessons.parse(tmp_path / ".forge" / "intel" / "lessons.md")
    assert parsed[0].status == "retired"


def test_amend_status_active_to_superseded(tmp_path: Path) -> None:
    lessons.append(tmp_path, _make_lesson(id="L001"))
    lessons.append(tmp_path, _make_lesson(id="L002"))
    lessons.amend_status(tmp_path, "L001", "superseded-by:L002")
    parsed = lessons.parse(tmp_path / ".forge" / "intel" / "lessons.md")
    assert parsed[0].status == "superseded-by:L002"


def test_amend_status_superseded_missing_target_raises(tmp_path: Path) -> None:
    lessons.append(tmp_path, _make_lesson(id="L001"))
    with pytest.raises(lessons.LessonError, match="L999"):
        lessons.amend_status(tmp_path, "L001", "superseded-by:L999")


def test_amend_status_superseded_to_retired_target_works(tmp_path: Path) -> None:
    lessons.append(tmp_path, _make_lesson(id="L001"))
    lessons.append(tmp_path, _make_lesson(id="L002"))
    lessons.amend_status(tmp_path, "L002", "retired")
    lessons.amend_status(tmp_path, "L001", "superseded-by:L002")
    parsed = lessons.parse(tmp_path / ".forge" / "intel" / "lessons.md")
    assert parsed[0].status == "superseded-by:L002"


def test_amend_status_chain_rejected(tmp_path: Path) -> None:
    lessons.append(tmp_path, _make_lesson(id="L001"))
    lessons.append(tmp_path, _make_lesson(id="L002"))
    lessons.append(tmp_path, _make_lesson(id="L003"))
    lessons.amend_status(tmp_path, "L002", "superseded-by:L003")
    with pytest.raises(lessons.LessonError, match=r"chain|superseded"):
        lessons.amend_status(tmp_path, "L001", "superseded-by:L002")


def test_amend_status_retired_to_active(tmp_path: Path) -> None:
    lessons.append(tmp_path, _make_lesson(id="L001"))
    lessons.amend_status(tmp_path, "L001", "retired")
    lessons.amend_status(tmp_path, "L001", "active")
    parsed = lessons.parse(tmp_path / ".forge" / "intel" / "lessons.md")
    assert parsed[0].status == "active"


def test_amend_status_bad_status_raises(tmp_path: Path) -> None:
    lessons.append(tmp_path, _make_lesson(id="L001"))
    with pytest.raises(lessons.LessonError, match=r"[Ss]tatus"):
        lessons.amend_status(tmp_path, "L001", "deprecated")


def test_amend_status_missing_lesson_raises(tmp_path: Path) -> None:
    lessons.append(tmp_path, _make_lesson(id="L001"))
    with pytest.raises(lessons.LessonError, match="L042"):
        lessons.amend_status(tmp_path, "L042", "retired")


# ---------------------------------------------------------------------------
# to_budget_dict locked shape
# ---------------------------------------------------------------------------


def test_to_budget_dict_locked_shape(tmp_path: Path) -> None:
    lessons.append(tmp_path, _make_lesson(id="L001"))
    parsed = lessons.parse(tmp_path / ".forge" / "intel" / "lessons.md")
    payload = parsed[0].to_budget_dict()
    assert set(payload.keys()) == {"id", "trap", "avoidance", "tags", "severity", "status"}
    assert payload["id"] == "L001"
    assert payload["tags"] == ["dispatch", "validation"]
    assert "body_words" not in payload
    assert "captured" not in payload


def test_parse_refuses_oversize_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Files larger than the cap raise LessonError before parsing kicks in."""
    path = tmp_path / ".forge" / "intel" / "lessons.md"
    path.parent.mkdir(parents=True)
    # Lower the cap to a tiny value so we don't have to create a 1 MiB file.
    monkeypatch.setattr(lessons, "_MAX_LESSONS_FILE_BYTES", 128)
    path.write_text(_HEADER + ("x" * 256), encoding="utf-8")
    with pytest.raises(lessons.LessonError, match="refuse to parse"):
        lessons.parse(path)


def test_parse_refuses_oversize_trap_field(tmp_path: Path) -> None:
    """Trap longer than the 1000-char cap blocks parse."""
    long_trap = "x" * 1001
    body = _file(_entry(trap=long_trap))
    path = _write(tmp_path / "lessons.md", body)
    with pytest.raises(lessons.LessonError, match=r"Trap field is 1001 chars"):
        lessons.parse(path)


def test_parse_empty_file_returns_empty_list(tmp_path: Path) -> None:
    """A zero-byte file parses to an empty lesson list (no header required)."""
    path = _write(tmp_path / "lessons.md", "")
    assert lessons.parse(path) == []


def test_parse_frontmatter_only_returns_empty_list(tmp_path: Path) -> None:
    """A header-only file with no lesson entries returns an empty list."""
    path = _write(tmp_path / "lessons.md", _HEADER)
    assert lessons.parse(path) == []


def test_parse_handles_crlf_line_endings(tmp_path: Path) -> None:
    """Files saved with Windows-style CRLF must still parse cleanly."""
    body = _file(_entry(nid="L001")).replace("\n", "\r\n")
    path = _write(tmp_path / "lessons.md", body)
    parsed = lessons.parse(path)
    assert len(parsed) == 1
    assert parsed[0].id == "L001"


def test_parse_handles_header_shaped_trap_prose(tmp_path: Path) -> None:
    """A Trap that starts with '##' must not produce a phantom second header.

    The parser's _FIELD_RE matches first, so the line is captured as the
    Trap field value rather than a fresh lesson header. The serialiser's
    inline-markdown sanitiser later trims the leading '##' from the title
    derivation; we just need the parse path to be loud-or-silent, not
    bewildering.
    """
    body = _file(
        _entry(nid="L001", trap="## sneaky heading shape — looks like a header"),
    )
    path = _write(tmp_path / "lessons.md", body)
    parsed = lessons.parse(path)
    assert len(parsed) == 1
    assert "sneaky heading shape" in parsed[0].trap


def test_amend_status_retired_to_superseded(tmp_path: Path) -> None:
    """retired -> superseded-by:L<NNN> is an allowed transition."""
    body = _file(
        _entry(nid="L001", status="retired"),
        _entry(nid="L002"),
    )
    _write(tmp_path / ".forge" / "intel" / "lessons.md", body)
    lessons.amend_status(tmp_path, "L001", "superseded-by:L002")
    parsed = lessons.parse(tmp_path / ".forge" / "intel" / "lessons.md")
    assert parsed[0].status == "superseded-by:L002"


def test_parse_error_includes_source_line_number(tmp_path: Path) -> None:
    """Per-block parser errors must name the source line of the offending header.

    The template prelude is 6 lines (frontmatter + heading + blank); the
    second lesson header therefore lands on line 17 in this fixture. The
    line number gives authors a direct navigation target instead of forcing
    them to grep for the lesson id by hand.
    """
    body = _file(
        _entry(nid="L001"),
        _entry(nid="L002", severity="WARN"),  # bad severity on the second entry
    )
    path = _write(tmp_path / "lessons.md", body)
    with pytest.raises(lessons.LessonError, match=r"line \d+ lesson L002") as exc:
        lessons.parse(path)
    assert "Severity" in str(exc.value)


def test_parse_refuses_oversize_avoidance_field(tmp_path: Path) -> None:
    """Avoidance longer than the 1000-char cap blocks parse."""
    long_avoidance = "y" * 1001
    body = _file(_entry(avoidance=long_avoidance))
    path = _write(tmp_path / "lessons.md", body)
    with pytest.raises(lessons.LessonError, match=r"Avoidance field is 1001 chars"):
        lessons.parse(path)


def test_parse_accepts_exactly_1000_char_field(tmp_path: Path) -> None:
    """The 1000-char cap is inclusive — exactly 1000 must parse."""
    body = _file(_entry(trap="x" * 1000))
    path = _write(tmp_path / "lessons.md", body)
    parsed = lessons.parse(path)
    assert len(parsed[0].trap) == 1000


def test_append_refuses_concurrent_writer_under_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hold the sidecar lock externally; the second appender must refuse loudly.

    Skipped when ``fcntl`` is unavailable (Windows) — the no-fcntl fallback
    relies on a different race-narrow check exercised by the next test.
    """
    if lessons.fcntl is None:
        pytest.skip("fcntl unavailable on this platform")
    lock_path = tmp_path / ".forge" / "intel" / "lessons.md.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    held_fh = lock_path.open("w")
    lessons.fcntl.flock(held_fh, lessons.fcntl.LOCK_EX | lessons.fcntl.LOCK_NB)
    try:
        draft = _make_lesson(id="L001")
        with pytest.raises(lessons.LessonError, match="another lesson append is in flight"):
            lessons.append(tmp_path, draft)
    finally:
        held_fh.close()


def test_append_post_check_refuses_when_slot_was_filled_concurrently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Race narrow: another writer fills the slot between body-build and rename.

    Simulate the race by patching ``next_id`` to return the same slot on the
    first call (allocator) but a higher slot on the second call (post-check).
    The post-check inside :func:`append` should detect the drift and refuse
    rather than silently clobber the concurrent writer's entry.
    """
    call_count = {"n": 0}

    def drifting_next_id(repo_root: Path) -> str:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return "L001"  # allocator returns L001 to the appender
        return "L002"  # post-check sees a concurrent writer took L001

    monkeypatch.setattr(lessons, "next_id", drifting_next_id)
    # Disable fcntl so the post-check is the only safety net firing.
    monkeypatch.setattr(lessons, "fcntl", None)
    # Stop the real atomic_replace from running — we only care that the
    # post-check raises before it would have been called.
    monkeypatch.setattr(
        lessons,
        "atomic_replace",
        lambda *_a, **_kw: pytest.fail("atomic_replace must not run on drift detection"),
    )

    draft = _make_lesson(id="L001", trap="original.")
    with pytest.raises(lessons.LessonError, match="concurrent append detected"):
        lessons.append(tmp_path, draft)

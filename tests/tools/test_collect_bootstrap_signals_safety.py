"""Safety regression tests for tools.constitution_amend signal collection.

Covers three hardening areas that the older test file does not exercise:

* Symlink containment in ``_candidate_paths`` — symlinks that resolve outside
  ``repo_root`` must not be read into the bootstrap payload.
* UTF-8-safe truncation in ``_read_and_truncate`` — the 16 KiB cap must back
  off to a codepoint boundary so the decoded body never contains a U+FFFD
  replacement char produced by a half-decoded multi-byte sequence.
* Per-collect nonced truncation marker — a user file whose body legitimately
  contains the legacy literal must not be confused with the marker, and
  callers may pin ``nonce_hex`` to recover byte-equality across runs.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath

import pytest

from tools.constitution_amend import (
    _PER_FILE_CAP_BYTES,
    BootstrapSignals,
    collect_bootstrap_signals,
    collect_resync_signals,
)


def _write_bytes(repo: Path, rel: str, body: bytes) -> Path:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return target


def _write_text(repo: Path, rel: str, body: str) -> Path:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


def _marker_for(signals: BootstrapSignals) -> str:
    """Return the truncation marker string used by ``signals``.

    The marker is whatever was appended to a truncated file's body — pull
    it back out of the first truncated SignalFile so tests do not have to
    re-derive the per-collect nonce.
    """
    for sf in signals.files:
        if sf.truncated:
            # marker is suffix starting at the last "\n--- truncated"
            idx = sf.body.rfind("\n--- truncated")
            assert idx >= 0, "truncated file body missing marker prefix"
            return sf.body[idx:]
    raise AssertionError("signals has no truncated files; marker undefined")


# --- Symlink containment ----------------------------------------------------


def test_symlink_to_file_inside_repo_root_is_collected_normally(tmp_path: Path) -> None:
    real = _write_text(tmp_path, "real_doc.md", "# real content\n")
    link = tmp_path / "README.md"
    link.symlink_to(real)

    result = collect_bootstrap_signals(tmp_path)

    rels = [f.relative_path for f in result.files]
    assert PurePosixPath("README.md") in rels
    body = next(f.body for f in result.files if f.relative_path == PurePosixPath("README.md"))
    assert "real content" in body
    assert PurePosixPath("README.md") not in result.dropped_for_escape


def test_symlink_to_file_outside_repo_root_is_skipped_and_recorded(tmp_path: Path) -> None:
    outside_root = tmp_path.parent / f"{tmp_path.name}-outside"
    outside_root.mkdir()
    outside = outside_root / "secrets.txt"
    outside.write_text("OUT_OF_TREE_CONTENT\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").symlink_to(outside)
    _write_text(repo, "AGENTS.md", "# in-tree doc\n")

    result = collect_bootstrap_signals(repo)

    rels = [f.relative_path for f in result.files]
    assert PurePosixPath("README.md") not in rels
    assert PurePosixPath("README.md") in result.dropped_for_escape
    # in-tree neighbor still collected
    assert PurePosixPath("AGENTS.md") in rels
    # body of escaping file never appears anywhere in the payload
    assert all("OUT_OF_TREE_CONTENT" not in f.body for f in result.files)


def test_broken_symlink_is_skipped_silently(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.target"
    (tmp_path / "README.md").symlink_to(missing)
    _write_text(tmp_path, "AGENTS.md", "# in-tree doc\n")

    result = collect_bootstrap_signals(tmp_path)

    rels = [f.relative_path for f in result.files]
    assert PurePosixPath("README.md") not in rels
    assert PurePosixPath("README.md") not in result.dropped_for_escape
    assert PurePosixPath("AGENTS.md") in rels


def test_symlink_loop_is_skipped_without_raising(tmp_path: Path) -> None:
    a = tmp_path / "README.md"
    b = tmp_path / "loop_target"
    a.symlink_to(b)
    b.symlink_to(a)
    _write_text(tmp_path, "AGENTS.md", "# in-tree doc\n")

    result = collect_bootstrap_signals(tmp_path)

    rels = [f.relative_path for f in result.files]
    assert PurePosixPath("README.md") not in rels
    assert PurePosixPath("AGENTS.md") in rels


def test_dropped_for_escape_preserves_priority_order(tmp_path: Path) -> None:
    outside_root = tmp_path.parent / f"{tmp_path.name}-outside-prio"
    outside_root.mkdir()
    outside_a = outside_root / "a.txt"
    outside_a.write_text("a\n", encoding="utf-8")
    outside_b = outside_root / "b.txt"
    outside_b.write_text("b\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    # pyproject.toml is higher-priority than README.md in the manifest order.
    (repo / "pyproject.toml").symlink_to(outside_a)
    (repo / "README.md").symlink_to(outside_b)

    result = collect_bootstrap_signals(repo)

    assert result.dropped_for_escape == [
        PurePosixPath("pyproject.toml"),
        PurePosixPath("README.md"),
    ]


# --- UTF-8-safe truncation --------------------------------------------------


def test_file_exactly_at_cap_is_not_truncated(tmp_path: Path) -> None:
    body = "a" * _PER_FILE_CAP_BYTES
    _write_text(tmp_path, "README.md", body)

    result = collect_bootstrap_signals(tmp_path)

    assert len(result.files) == 1
    sf = result.files[0]
    assert sf.truncated is False
    assert len(sf.body.encode("utf-8")) == _PER_FILE_CAP_BYTES


def test_file_one_byte_over_cap_truncates_within_cap_plus_marker(tmp_path: Path) -> None:
    _write_text(tmp_path, "README.md", "a" * (_PER_FILE_CAP_BYTES + 1))

    result = collect_bootstrap_signals(tmp_path)
    marker = _marker_for(result)

    sf = result.files[0]
    assert sf.truncated is True
    body_bytes = sf.body.encode("utf-8")
    assert len(body_bytes) <= _PER_FILE_CAP_BYTES + len(marker.encode("utf-8"))


def test_two_byte_codepoint_straddling_cap_yields_no_replacement_char(
    tmp_path: Path,
) -> None:
    """Cap lands mid-codepoint for ``é`` (two-byte UTF-8). The decoded body
    must never carry a U+FFFD replacement char — back off to the last
    codepoint boundary instead.
    """
    # 16383 ASCII bytes + one two-byte 'é' (0xc3 0xa9) — last byte spills past cap.
    body_bytes = (b"a" * (_PER_FILE_CAP_BYTES - 1)) + "é".encode()
    _write_bytes(tmp_path, "README.md", body_bytes)

    result = collect_bootstrap_signals(tmp_path)

    sf = result.files[0]
    assert sf.truncated is True
    assert "�" not in sf.body, "decoded body contains U+FFFD replacement char"
    marker = _marker_for(result)
    assert len(sf.body.encode("utf-8")) <= _PER_FILE_CAP_BYTES + len(marker.encode("utf-8"))


def test_repeated_two_byte_codepoints_truncate_on_boundary(tmp_path: Path) -> None:
    # Fill past the cap with 'é' so the boundary lands mid-codepoint.
    payload = ("é" * (_PER_FILE_CAP_BYTES // 2 + 5)).encode("utf-8")
    _write_bytes(tmp_path, "README.md", payload)

    result = collect_bootstrap_signals(tmp_path)

    sf = result.files[0]
    assert sf.truncated is True
    assert "�" not in sf.body
    marker = _marker_for(result)
    assert len(sf.body.encode("utf-8")) <= _PER_FILE_CAP_BYTES + len(marker.encode("utf-8"))


def test_four_byte_codepoint_at_boundary_truncates_cleanly(tmp_path: Path) -> None:
    # Emoji (U+1F600) = 4 bytes in UTF-8 (0xf0 0x9f 0x98 0x80).
    # Land the cap inside the 4-byte sequence.
    head = b"a" * (_PER_FILE_CAP_BYTES - 2)
    payload = head + "😀😀".encode() + b"trailing"
    _write_bytes(tmp_path, "README.md", payload)

    result = collect_bootstrap_signals(tmp_path)

    sf = result.files[0]
    assert sf.truncated is True
    assert "�" not in sf.body
    marker = _marker_for(result)
    assert len(sf.body.encode("utf-8")) <= _PER_FILE_CAP_BYTES + len(marker.encode("utf-8"))


# --- Nonced truncation marker ----------------------------------------------


def test_marker_carries_bracketed_hex_nonce(tmp_path: Path) -> None:
    _write_text(tmp_path, "README.md", "z" * (_PER_FILE_CAP_BYTES + 1))

    result = collect_bootstrap_signals(tmp_path)
    marker = _marker_for(result)

    # Marker shape: leading newline, fixed prose, bracketed 16-hex nonce,
    # trailing prose, trailing newline.
    pattern = re.compile(
        rf"^\n--- truncated at {_PER_FILE_CAP_BYTES} bytes \[[0-9a-f]{{16}}\] ---\n$",
    )
    assert pattern.fullmatch(marker), f"marker shape unexpected: {marker!r}"


def test_two_default_collect_calls_produce_different_markers(tmp_path: Path) -> None:
    _write_text(tmp_path, "README.md", "z" * (_PER_FILE_CAP_BYTES + 1))

    first = collect_bootstrap_signals(tmp_path)
    second = collect_bootstrap_signals(tmp_path)

    assert _marker_for(first) != _marker_for(second)


def test_explicit_nonce_recovers_byte_equality(tmp_path: Path) -> None:
    _write_text(tmp_path, "README.md", "z" * (_PER_FILE_CAP_BYTES + 1))

    first = collect_bootstrap_signals(tmp_path, nonce_hex="deadbeefcafef00d")
    second = collect_bootstrap_signals(tmp_path, nonce_hex="deadbeefcafef00d")

    assert first == second
    assert _marker_for(first) == _marker_for(second)
    assert "[deadbeefcafef00d]" in _marker_for(first)


def test_legacy_unnonced_literal_in_user_content_is_not_confused(tmp_path: Path) -> None:
    """A README that legitimately contains the legacy unnonced literal must
    not be confused with the truncation marker — the new marker always
    carries the per-collect nonce, so the literal is just user content.
    """
    fake = f"\n--- truncated at {_PER_FILE_CAP_BYTES} bytes ---\n"
    body = "# Real Readme\n\n" + fake + "tail content after fake marker\n"
    _write_text(tmp_path, "README.md", body)

    result = collect_bootstrap_signals(tmp_path)

    sf = next(f for f in result.files if f.relative_path == PurePosixPath("README.md"))
    # The file was not actually truncated; the fake string is just part of
    # the body. The real marker (if any) has a nonce, so callers that scan
    # for the nonced shape will not match the fake.
    assert sf.truncated is False
    assert fake in sf.body  # body preserved verbatim


def test_resync_collector_also_accepts_nonce_hex(tmp_path: Path) -> None:
    _write_text(tmp_path, "README.md", "z" * (_PER_FILE_CAP_BYTES + 1))

    first = collect_resync_signals(tmp_path, nonce_hex="cafebabefeedface")
    second = collect_resync_signals(tmp_path, nonce_hex="cafebabefeedface")

    assert first == second
    marker = _marker_for(first)
    assert "[cafebabefeedface]" in marker


@pytest.mark.parametrize(
    "bad_nonce",
    ["", "g" * 16, "ZZZZ", "deadbeef!"],
)
def test_explicit_nonce_validates_hex_shape(tmp_path: Path, bad_nonce: str) -> None:
    """Caller-supplied ``nonce_hex`` must be lowercase hex — otherwise the
    helper raises rather than silently embedding stray characters into the
    marker (which downstream parsers may rely on as a regex-stable shape).
    """
    _write_text(tmp_path, "README.md", "z" * (_PER_FILE_CAP_BYTES + 1))

    with pytest.raises(ValueError):
        collect_bootstrap_signals(tmp_path, nonce_hex=bad_nonce)

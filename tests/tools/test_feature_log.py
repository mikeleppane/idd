"""Behavioral tests for the local-only JSONL feature event log writer."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import pytest

from tools.feature_log import (
    FeatureLogError,
    FeatureLogEvent,
    append_event,
    log_path,
    read_events,
)


def _event(
    *,
    feature_id: str = "2026-05-08-foo",
    event_type: str = "phase_started",
    timestamp: str = "2026-05-08T12:00:00Z",
    payload: dict[str, object] | None = None,
) -> FeatureLogEvent:
    return FeatureLogEvent(
        feature_id=feature_id,
        event_type=event_type,  # type: ignore[arg-type]
        timestamp=timestamp,
        payload=payload if payload is not None else {"phase": "spec"},
    )


def test_log_path_returns_expected_layout(tmp_path: Path) -> None:
    expected = tmp_path / ".forge" / "logs" / "2026-05-08-foo.jsonl"
    assert log_path(tmp_path, "2026-05-08-foo") == expected


def test_append_event_creates_parent_dir_lazily(tmp_path: Path) -> None:
    assert not (tmp_path / ".forge" / "logs").exists()

    append_event(tmp_path, _event())

    target = tmp_path / ".forge" / "logs" / "2026-05-08-foo.jsonl"
    assert target.is_file()
    assert (tmp_path / ".forge" / "logs").is_dir()


def test_append_event_writes_jsonl_one_line_per_event(tmp_path: Path) -> None:
    append_event(
        tmp_path,
        _event(timestamp="2026-05-08T12:00:00Z", payload={"phase": "spec"}),
    )
    append_event(
        tmp_path,
        _event(
            event_type="phase_completed",
            timestamp="2026-05-08T12:00:01Z",
            payload={"phase": "spec"},
        ),
    )
    append_event(
        tmp_path,
        _event(
            event_type="commit_recorded",
            timestamp="2026-05-08T12:00:02Z",
            payload={"sha": "abc1234", "subject": "feat(tools): foo"},
        ),
    )

    raw = (tmp_path / ".forge" / "logs" / "2026-05-08-foo.jsonl").read_text(encoding="utf-8")
    lines = raw.splitlines()
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert [p["event_type"] for p in parsed] == [
        "phase_started",
        "phase_completed",
        "commit_recorded",
    ]


def test_append_event_preserves_order(tmp_path: Path) -> None:
    events = [
        _event(timestamp="2026-05-08T12:00:00Z", payload={"i": 0}),
        _event(
            event_type="subagent_dispatched",
            timestamp="2026-05-08T12:00:01Z",
            payload={"i": 1},
        ),
        _event(
            event_type="subagent_returned",
            timestamp="2026-05-08T12:00:02Z",
            payload={"i": 2},
        ),
    ]
    for ev in events:
        append_event(tmp_path, ev)

    read_back = read_events(tmp_path, "2026-05-08-foo")
    assert [e.payload["i"] for e in read_back] == [0, 1, 2]
    assert [e.event_type for e in read_back] == [
        "phase_started",
        "subagent_dispatched",
        "subagent_returned",
    ]


def test_append_event_rejects_invalid_event_type(tmp_path: Path) -> None:
    bad = FeatureLogEvent(
        feature_id="2026-05-08-foo",
        event_type="frobnicated",  # type: ignore[arg-type]
        timestamp="2026-05-08T12:00:00Z",
        payload={"phase": "spec"},
    )

    with pytest.raises(FeatureLogError, match="event_type"):
        append_event(tmp_path, bad)

    assert not (tmp_path / ".forge" / "logs" / "2026-05-08-foo.jsonl").exists()


@pytest.mark.parametrize("payload", [None, ["a", "b"], "string", 42])
def test_append_event_rejects_non_dict_payload(tmp_path: Path, payload: object) -> None:
    bad = FeatureLogEvent(
        feature_id="2026-05-08-foo",
        event_type="phase_started",
        timestamp="2026-05-08T12:00:00Z",
        payload=payload,  # type: ignore[arg-type]
    )

    with pytest.raises(FeatureLogError, match="payload"):
        append_event(tmp_path, bad)

    assert not (tmp_path / ".forge" / "logs" / "2026-05-08-foo.jsonl").exists()


@pytest.mark.parametrize(
    "timestamp",
    ["", "2026-05-08T12:00:00", "not-a-timestamp", "2026-13-40T99:99:99Z"],
)
def test_append_event_rejects_invalid_timestamp(tmp_path: Path, timestamp: str) -> None:
    bad = FeatureLogEvent(
        feature_id="2026-05-08-foo",
        event_type="phase_started",
        timestamp=timestamp,
        payload={"phase": "spec"},
    )

    with pytest.raises(FeatureLogError, match="timestamp"):
        append_event(tmp_path, bad)

    assert not (tmp_path / ".forge" / "logs" / "2026-05-08-foo.jsonl").exists()


def test_append_event_rejects_invalid_feature_id(tmp_path: Path) -> None:
    bad = FeatureLogEvent(
        feature_id="not a feature id",
        event_type="phase_started",
        timestamp="2026-05-08T12:00:00Z",
        payload={"phase": "spec"},
    )

    with pytest.raises(FeatureLogError, match="feature_id"):
        append_event(tmp_path, bad)


def test_read_events_empty_when_file_missing(tmp_path: Path) -> None:
    assert read_events(tmp_path, "2026-05-08-foo") == []


def test_read_events_raises_on_malformed_jsonl(tmp_path: Path) -> None:
    target = log_path(tmp_path, "2026-05-08-foo")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{not json}\n", encoding="utf-8")

    with pytest.raises(FeatureLogError, match="malformed log entry at line 1"):
        read_events(tmp_path, "2026-05-08-foo")


def test_append_event_no_network_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("feature_log opened a socket — module must be local-file I/O only")

    monkeypatch.setattr(socket, "socket", _boom)

    append_event(tmp_path, _event())

    target = tmp_path / ".forge" / "logs" / "2026-05-08-foo.jsonl"
    assert target.is_file()


@pytest.mark.parametrize(
    "bad_id",
    [
        "../../escape",
        "..",
        "2026-05-08-foo/../../etc",
        "2026-05-08-foo\x00",
        "abs/path",
        "",
    ],
)
def test_log_path_rejects_traversal_feature_ids(tmp_path: Path, bad_id: str) -> None:
    """``feature_id`` is a trusted path segment — non-conforming ids must
    raise before any path math runs."""
    with pytest.raises(FeatureLogError, match="feature_id"):
        log_path(tmp_path, bad_id)


def test_read_events_rejects_traversal_feature_id(tmp_path: Path) -> None:
    """``read_events`` must validate ``feature_id`` before path construction
    so an attacker-controlled id cannot walk out of ``.forge/logs/``."""
    with pytest.raises(FeatureLogError, match="feature_id"):
        read_events(tmp_path, "../../escape")


def test_read_events_rejects_corrupted_event_payload(tmp_path: Path) -> None:
    """A JSONL line that round-trips into a Finding with bad event_type
    must raise — read_events re-validates reconstructed events."""
    target = log_path(tmp_path, "2026-05-08-foo")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Bypass the writer's validator by writing the JSONL directly with a
    # bogus event_type. read_events must reject it.
    target.write_text(
        json.dumps(
            {
                "feature_id": "2026-05-08-foo",
                "event_type": "frobnicated",
                "timestamp": "2026-05-08T12:00:00Z",
                "payload": {"phase": "spec"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(FeatureLogError, match="line 1"):
        read_events(tmp_path, "2026-05-08-foo")


def test_read_events_rejects_corrupted_payload_type(tmp_path: Path) -> None:
    """A JSONL line with ``payload`` that is not a dict must raise."""
    target = log_path(tmp_path, "2026-05-08-foo")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "feature_id": "2026-05-08-foo",
                "event_type": "phase_started",
                "timestamp": "2026-05-08T12:00:00Z",
                "payload": ["not", "a", "dict"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(FeatureLogError, match="line 1"):
        read_events(tmp_path, "2026-05-08-foo")


def test_append_event_invokes_single_write_per_event(tmp_path: Path) -> None:
    """``append_event`` must serialize each event in ONE underlying ``write``.

    Two separate ``write`` calls (line + trailing newline) leave the JSONL
    file in a partially-written state if the second call fails (disk-full,
    interrupted system call, signal): the file ends with a JSON line missing
    its terminating ``\\n``, which silently corrupts the boundaries that
    ``read_events`` walks. A single combined ``write`` of ``serialized + "\\n"``
    is atomic at the OS level for sub-blocksize buffers and removes the race.

    The test wraps the file handle returned by ``Path.open`` and tracks every
    ``write`` call against the log target.
    """
    target = log_path(tmp_path, "2026-05-08-foo")
    write_calls: list[str] = []

    real_open: Any = Path.open

    def counted_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        handle: Any = real_open(self, *args, **kwargs)
        if self == target:
            real_write = handle.write

            def counting_write(data: str) -> int:
                write_calls.append(data)
                return int(real_write(data))

            handle.write = counting_write
        return handle

    saved: Any = Path.open
    Path.open = counted_open  # type: ignore[method-assign]
    try:
        append_event(tmp_path, _event())
    finally:
        Path.open = saved  # type: ignore[method-assign]

    assert len(write_calls) == 1, (
        f"expected exactly one write per append_event; got {len(write_calls)}: {write_calls!r}"
    )
    assert write_calls[0].endswith("\n"), write_calls[0]


def test_append_event_round_trips_unicode_payload(tmp_path: Path) -> None:
    payload: dict[str, object] = {"summary": "résumé naïve — 日本語 🚀"}
    append_event(
        tmp_path,
        _event(payload=payload),
    )

    read_back = read_events(tmp_path, "2026-05-08-foo")
    assert len(read_back) == 1
    assert read_back[0].payload == payload

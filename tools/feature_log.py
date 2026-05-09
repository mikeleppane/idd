"""Append-only JSONL event log for feature lifecycle events.

Events land at ``<repo_root>/.forge/logs/<feature_id>.jsonl`` — one event
per line, never re-serialized as a whole file. The writer is purely local
file I/O: no network sockets, no subprocess, no remote sink. The log is
gitignored at the repo level.

Three pure functions form the contract:

* :func:`log_path` returns the canonical on-disk location for a feature's
  log without touching the filesystem.
* :func:`append_event` validates a :class:`FeatureLogEvent`, serializes it
  to a single compact JSON line, and appends it to the log (creating the
  parent directory and file lazily on first call).
* :func:`read_events` parses the JSONL file back into a list of events
  preserving insertion order; an empty list is returned when the file is
  missing.

Validation failures raise :class:`FeatureLogError`. The validator does
not care about the event_type-specific shape of ``payload`` beyond
"is a dict" — downstream consumers may layer richer schemas on top.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal, get_args

# Mirrored from ``tools.state._FEATURE_ID_RE``. Duplicated minimally
# because the upstream constant is a private symbol and we want this
# module to stay flat / dependency-light. If the canonical regex ever
# moves to a public helper in ``tools.state``, swap this for the import.
_FEATURE_ID_RE: Final[re.Pattern[str]] = re.compile(
    r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])-[a-z0-9-]+$"
)

EventType = Literal[
    "phase_started",
    "phase_completed",
    "phase_skipped",
    "subagent_dispatched",
    "subagent_returned",
    "commit_recorded",
    "deviation_logged",
    "decision_logged",
]

_VALID_EVENT_TYPES: Final[frozenset[str]] = frozenset(get_args(EventType))


class FeatureLogError(RuntimeError):
    """Raised when an event fails validation or a log line cannot be parsed."""


@dataclass(frozen=True)
class FeatureLogEvent:
    """A single feature lifecycle event destined for the JSONL log.

    Attributes:
        feature_id: ``YYYY-MM-DD-slug`` feature identifier; must match the
            canonical feature-id regex shared with :mod:`tools.state`.
        event_type: One of the :data:`EventType` literal values.
        timestamp: ISO 8601 UTC timestamp ending with ``Z`` (e.g.
            ``2026-05-08T12:00:00Z``).
        payload: Event-type-specific structured data; must be a ``dict``,
            never ``None``.
    """

    feature_id: str
    event_type: EventType
    timestamp: str
    payload: dict[str, Any]


def _validate_feature_id(feature_id: str) -> None:
    """Reject feature ids that would escape ``.forge/logs/`` via path traversal.

    Both :func:`append_event` and :func:`read_events` rely on ``feature_id``
    as a trusted path segment. Validating it before any path math is the
    cheapest defense against ``../../../etc/passwd``-style payloads.
    """
    if not isinstance(feature_id, str) or not _FEATURE_ID_RE.fullmatch(feature_id):
        raise FeatureLogError(f"invalid feature_id {feature_id!r}; must match YYYY-MM-DD-slug")


def log_path(repo_root: Path, feature_id: str) -> Path:
    """Return the canonical log path for ``feature_id`` under ``repo_root``.

    Pure function — does not touch the filesystem. The ``feature_id`` is
    validated against the canonical regex before path construction so an
    attacker-controlled id cannot traverse outside ``.forge/logs/``.

    Args:
        repo_root: Repository root containing the ``.forge/`` tree.
        feature_id: ``YYYY-MM-DD-slug`` feature identifier.

    Returns:
        Path to ``<repo_root>/.forge/logs/<feature_id>.jsonl``.

    Raises:
        FeatureLogError: When ``feature_id`` does not match the canonical
            ``YYYY-MM-DD-slug`` regex.
    """
    _validate_feature_id(feature_id)
    return repo_root / ".forge" / "logs" / f"{feature_id}.jsonl"


def _validate_event(event: FeatureLogEvent) -> None:
    _validate_feature_id(event.feature_id)

    if event.event_type not in _VALID_EVENT_TYPES:
        raise FeatureLogError(
            f"invalid event_type {event.event_type!r}; must be one of {sorted(_VALID_EVENT_TYPES)}"
        )

    if not isinstance(event.timestamp, str) or not event.timestamp:
        raise FeatureLogError("invalid timestamp: must be a non-empty ISO 8601 UTC string")
    if not event.timestamp.endswith("Z"):
        raise FeatureLogError(f"invalid timestamp {event.timestamp!r}: must end with 'Z' (UTC)")
    try:
        datetime.fromisoformat(event.timestamp[:-1])
    except ValueError as exc:
        raise FeatureLogError(f"invalid timestamp {event.timestamp!r}: {exc}") from exc

    # ``bool`` is a subclass of ``int`` but not ``dict``; explicit isinstance
    # check covers None / list / str / int / bool uniformly.
    if not isinstance(event.payload, dict):
        raise FeatureLogError(
            f"invalid payload: must be a dict, got {type(event.payload).__name__}"
        )


def append_event(repo_root: Path, event: FeatureLogEvent) -> None:
    """Validate ``event`` and append it as a single JSON line to the log.

    The parent directory ``<repo_root>/.forge/logs/`` is created lazily on
    first append. The log file is opened in append mode with UTF-8
    encoding and an explicit LF newline. Validation failures raise
    :class:`FeatureLogError` *before* any disk write so the log file is
    never touched on a rejected event.

    Args:
        repo_root: Repository root containing the ``.forge/`` tree.
        event: The :class:`FeatureLogEvent` to record.

    Raises:
        FeatureLogError: When ``event`` fails feature-id, event-type,
            timestamp, or payload validation. The log file is unchanged.
    """
    _validate_event(event)

    serialized = json.dumps(
        {
            "feature_id": event.feature_id,
            "event_type": event.event_type,
            "timestamp": event.timestamp,
            "payload": event.payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    target = log_path(repo_root, event.feature_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as fh:
        # Single combined write: a partial-write failure (disk-full, signal)
        # leaves the file either fully extended by this line or untouched —
        # never with a JSON record missing its terminating newline.
        fh.write(serialized + "\n")


def read_events(repo_root: Path, feature_id: str) -> list[FeatureLogEvent]:
    """Parse the JSONL log for ``feature_id`` and return events in order.

    Returns an empty list when the log file does not exist. Each non-empty
    line is parsed as JSON and rebuilt into a :class:`FeatureLogEvent`.
    Malformed lines raise :class:`FeatureLogError` with a message that
    names the offending line number.

    Args:
        repo_root: Repository root containing the ``.forge/`` tree.
        feature_id: ``YYYY-MM-DD-slug`` feature identifier.

    Returns:
        List of :class:`FeatureLogEvent` in chronological insertion order.

    Raises:
        FeatureLogError: When a line cannot be parsed as JSON or does not
            carry the required fields.
    """
    target = log_path(repo_root, feature_id)
    if not target.exists():
        return []

    events: list[FeatureLogEvent] = []
    raw = target.read_text(encoding="utf-8")
    for index, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise FeatureLogError(f"malformed log entry at line {index}: {exc.msg}") from exc
        if not isinstance(data, dict):
            raise FeatureLogError(f"malformed log entry at line {index}: expected JSON object")
        try:
            event = FeatureLogEvent(
                feature_id=data["feature_id"],
                event_type=data["event_type"],
                timestamp=data["timestamp"],
                payload=data["payload"],
            )
        except KeyError as exc:
            raise FeatureLogError(
                f"malformed log entry at line {index}: missing field {exc.args[0]!r}"
            ) from exc
        try:
            _validate_event(event)
        except FeatureLogError as exc:
            raise FeatureLogError(f"malformed log entry at line {index}: {exc}") from exc
        events.append(event)
    return events

r"""Bring-your-own-docs loader for the research phase.

Users may pre-stage local documentation under ``.forge/external-docs/<lib>.md``
when Context7 is unavailable (air-gapped environments, opt-out users,
networks where the MCP server is blocked). This module is the single
entry-point Python uses to read those files.

Design constraints
------------------

* No subprocess. No git. The loader reads the file directly via
  ``pathlib.Path.read_bytes()`` and decodes UTF-8.
* UTC everywhere. Stat-derived mtimes are produced as ``datetime`` with
  ``tz=UTC`` so callers never see naive local-time values.
* Token-budget truncation. The loader caps the body at ``max_chars``
  (≈3000 tokens at the conservative 4-chars-per-token heuristic),
  truncating at the nearest preceding paragraph break (``\\n\\n``) so
  the subagent never sees a sentence chopped mid-word. If no paragraph
  boundary fits inside the budget the body is emptied but ``truncated``
  is still ``True`` so the caller can surface the rejection.
* Unreadable bytes (non-UTF-8) collapse to ``error="UNREADABLE"`` with
  an empty body — never raise. This keeps a single corrupted BYOD file
  from blocking the whole research run.
* Missing path is the only condition that raises (``ByodLoadError``):
  the caller asked for something we cannot represent.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


class ByodLoadError(Exception):
    """Raised when the requested BYOD path does not exist."""


@dataclass(frozen=True)
class LoadResult:
    """Outcome of a single BYOD file load."""

    body: str
    truncated: bool
    mtime: datetime
    stale: bool
    error: str | None


def _truncate_at_paragraph(body: str, max_chars: int) -> tuple[str, bool]:
    if len(body) <= max_chars:
        return body, False
    head = body[:max_chars]
    boundary = head.rfind("\n\n")
    if boundary == -1:
        return "", True
    return body[:boundary], True


def load(
    path: Path,
    *,
    stale_after_days: int = 90,
    now: datetime | None = None,
    max_chars: int = 12000,
) -> LoadResult:
    """Read a BYOD doc and return a :class:`LoadResult`.

    Raises :class:`ByodLoadError` if ``path`` does not exist. UTF-8
    decode failures collapse to ``error="UNREADABLE"`` with an empty
    body. Bodies exceeding ``max_chars`` are truncated at the nearest
    preceding paragraph break.
    """
    if not path.exists():
        raise ByodLoadError(f"BYOD doc not found: {path}")

    raw = path.read_bytes()
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    reference_now = now if now is not None else datetime.now(UTC)
    threshold = reference_now - timedelta(days=stale_after_days)
    stale = mtime < threshold

    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        return LoadResult(
            body="",
            truncated=False,
            mtime=mtime,
            stale=stale,
            error="UNREADABLE",
        )

    body, truncated = _truncate_at_paragraph(decoded, max_chars)
    return LoadResult(
        body=body,
        truncated=truncated,
        mtime=mtime,
        stale=stale,
        error=None,
    )

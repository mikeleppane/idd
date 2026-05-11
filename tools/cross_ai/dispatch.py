"""Auto-mode dispatch helpers for the cross-AI substrate.

The skill (``forge-review``) drives manual mode by hand; auto mode
hands the same per-target prompt to a subprocess instead of the
operator. This module is the mechanical layer the auto branch calls
into. Three helpers, all sync I/O — no asyncio, no event loop, no
streaming surface — because the dispatcher is invoked from inside a
slash-command body where the surrounding harness already sequences the
work.

Public surface (in ``__all__``)
-------------------------------

* :func:`auto_dispatch` — spawn an external reviewer CLI via
  :func:`subprocess.run`, pipe ``prompt_text`` on stdin, capture stdout
  into a :class:`DispatchResult`. Three failure axes route differently
  on purpose:

  - :class:`subprocess.CalledProcessError` (non-zero exit) **does**
    retry up to ``RetryPolicy.max + 1`` total attempts with
    ``sleep(backoff_seconds)`` between them. A flaky external CLI is
    the single case where a retry is reasonable.
  - :class:`subprocess.TimeoutExpired` does **not** retry — a hung
    CLI past ``timeout_seconds`` is structurally stuck; another shell
    out only compounds the wait.
  - :class:`OSError` (binary missing, unexecutable, etc.) does **not**
    retry — the binary is structurally unavailable; retrying cannot
    change the outcome.

  Every failure is wrapped in :class:`DispatchError` with the original
  exception preserved through the standard ``__cause__`` chain so the
  caller can route the deviation message correctly without parsing the
  message string.
* :func:`record_dispatch_approval` — atomic write of the
  ``cross_ai.dispatch_approved_at`` + ``dispatch_approved_by`` markers
  to ``.forge/config.json``. Idempotent: when the field is already
  populated the helper returns without rewriting the file (the on-disk
  bytes stay byte-identical so file watchers do not fire spuriously).
* :func:`write_response_to_disk` — atomic write of a captured
  reviewer response to
  ``.forge/features/<feature_id>/cross-ai/<target>-<utc>-response.md``.
  Mirrors the :func:`tools.cross_ai.manual.write_prompt_to_disk`
  shape so the two artifacts sit side-by-side in the same directory
  with parallel filename conventions.

Caller responsibilities
-----------------------

This module never:

* invokes a CLI other than the one named in ``cli=`` (no shell
  interpretation — the argv is literally ``[cli]``);
* mutates :class:`tools.cross_ai.config.RetryPolicy` (the dataclass
  is frozen by construction);
* parses the captured ``response_text`` (parsing is the parser's job;
  the dispatcher only carries bytes);
* validates ``cli`` against the allow-list (that gate fires earlier in
  the skill, not here).

Atomicity
---------

:func:`record_dispatch_approval` and :func:`write_response_to_disk`
both delegate to :func:`tools.cross_ai.manual._atomic_write_text` so
prompt, response, and config writes share one durability primitive
(``tempfile.mkstemp`` in the destination's parent directory followed
by :meth:`Path.replace`). Sharing the helper is deliberate — the two
modules cannot diverge on temp-file collision-avoidance or torn-write
cleanup without breaking the cross-AI directory's atomicity contract.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from tools.cross_ai.config import RetryPolicy
from tools.cross_ai.manual import _atomic_write_text
from tools.cross_ai.prompt import PromptTarget

__all__ = (
    "DispatchError",
    "DispatchResult",
    "auto_dispatch",
    "record_dispatch_approval",
    "write_response_to_disk",
)


# Filesystem timestamp shape — colons in the canonical ISO-8601 string
# are replaced with hyphens so the resulting filename is portable across
# filesystems that reject ``:`` (notably Windows / SMB shares). Mirrors
# ``tools.cross_ai.manual._TIMESTAMP_FMT`` deliberately so prompt and
# response filenames agree on the convention.
_TIMESTAMP_FMT: str = "%Y-%m-%dT%H-%M-%SZ"


class DispatchError(RuntimeError):
    """Raised when :func:`auto_dispatch` cannot produce a successful result.

    The underlying cause (``subprocess.TimeoutExpired`` /
    ``subprocess.CalledProcessError`` / ``OSError``) is preserved via
    the standard ``__cause__`` chain so the caller can route the
    deviation message without parsing the human-readable message
    string. The message itself records the structural failure mode
    (``"timed out after Ns"`` / ``"failed after N attempts (exit C)"``
    / ``"could not invoke <cli>"``) so a log line is self-explanatory
    even without inspecting the cause.
    """


@dataclass(frozen=True)
class DispatchResult:
    """Successful dispatch outcome.

    Frozen so the caller cannot mutate the snapshot — every field is a
    direct measurement of the subprocess invocation that produced it.

    Attributes:
        cli: The CLI binary name that was invoked (basename or path,
            verbatim from the ``cli=`` argument).
        response_text: Captured stdout, decoded as UTF-8 text. Stderr
            is intentionally NOT included — the spec contract is that
            the dispatcher carries only the reviewer's response, not
            its log noise.
        exit_code: Always ``0`` for a successful dispatch (a non-zero
            exit raises :class:`DispatchError` instead). Kept on the
            dataclass for symmetry with the failure path's structured
            data.
        attempts: Number of subprocess invocations performed. ``1`` on
            first-try success; ``> 1`` when a transient
            ``CalledProcessError`` retried before succeeding.
        elapsed_seconds: Wall-clock duration from the first invocation
            to the final success, measured via :func:`time.monotonic`
            so it is immune to system clock adjustments.
    """

    cli: str
    response_text: str
    exit_code: int
    attempts: int
    elapsed_seconds: float


def auto_dispatch(
    cli: str,
    prompt_text: str,
    timeout_seconds: int = 120,
    retry: RetryPolicy | None = None,
    *,
    env: dict[str, str] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> DispatchResult:
    """Spawn ``cli`` via :func:`subprocess.run`, pipe ``prompt_text``, capture stdout.

    Sync execution — no asyncio, no streaming. The caller (the
    forge-review skill's auto-mode branch) blocks on the return value
    so threading buys nothing here. ``argv`` is literally ``[cli]`` —
    no shell interpretation, no flag parsing — so a CLI name with
    embedded shell metacharacters cannot escape into the parent
    shell's grammar.

    Retry semantics are split by failure mode on purpose:

    * :class:`subprocess.CalledProcessError` retries up to
      ``retry.max + 1`` total attempts with
      ``sleep(retry.backoff_seconds)`` between them. A non-zero exit
      is the one failure where a retry has a chance of succeeding (CLI
      hit a transient rate limit, network blip, etc.).
    * :class:`subprocess.TimeoutExpired` does NOT retry — a hung CLI
      past ``timeout_seconds`` is structurally stuck; re-running it
      compounds the wait without changing the outcome.
    * :class:`OSError` (e.g. :class:`FileNotFoundError` when the binary
      is missing) does NOT retry — the binary is structurally
      unavailable; retrying cannot change that.

    Args:
        cli: Reviewer CLI binary name or path. Looked up via the
            inherited ``PATH`` (or the ``PATH`` entry in ``env=`` when
            the caller passes one).
        prompt_text: Markdown prompt body fed to the CLI's stdin.
        timeout_seconds: Wall-clock budget per invocation. Counts
            against each attempt independently, not the cumulative
            time across retries.
        retry: Per-call retry policy. ``None`` defaults to
            ``RetryPolicy()`` (the dataclass's documented defaults).
        env: Process environment for the subprocess. ``None`` inherits
            the parent process's env (default
            :func:`subprocess.run` behavior). Tests pass a
            fixture-scoped env so the mock CLI selects its canned
            response branch via ``MOCK_CLI_RESPONSE``.
        sleep: Backoff seam for tests. ``None`` defaults to
            :func:`time.sleep`. Test code injects a list-append callable
            to assert the backoff schedule without burning real seconds.

    Returns:
        :class:`DispatchResult` carrying the captured stdout, the
        attempt count, and the wall-clock elapsed time.

    Raises:
        DispatchError: Wrapped failure with the underlying exception
            preserved on ``__cause__``. Message records the structural
            failure mode (``"timed out after Ns"`` /
            ``"failed after N attempts (exit C)"`` /
            ``"could not invoke <cli>"``).
    """
    effective_retry = retry if retry is not None else RetryPolicy()
    effective_sleep = sleep if sleep is not None else time.sleep

    attempts = 0
    start = time.monotonic()
    max_attempts = effective_retry.max + 1

    while attempts < max_attempts:
        attempts += 1
        try:
            result = subprocess.run(
                [cli],
                input=prompt_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                check=True,
                timeout=timeout_seconds,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            # Hung CLI — retry compounds risk without changing the outcome.
            raise DispatchError(f"auto_dispatch timed out after {timeout_seconds}s") from exc
        except subprocess.CalledProcessError as exc:
            if attempts < max_attempts:
                effective_sleep(float(effective_retry.backoff_seconds))
                continue
            raise DispatchError(
                f"auto_dispatch failed after {attempts} attempts (exit {exc.returncode})"
            ) from exc
        except OSError as exc:
            # Binary missing / unexecutable / etc. — structurally unavailable.
            raise DispatchError(f"auto_dispatch could not invoke {cli!r}") from exc
        else:
            return DispatchResult(
                cli=cli,
                response_text=result.stdout,
                exit_code=0,
                attempts=attempts,
                elapsed_seconds=time.monotonic() - start,
            )

    # Defensive guard: the loop above either returns on success or
    # raises on every failure path. Reaching this line means the loop
    # bound was set such that the body never ran — a programming
    # error in this module rather than anything the caller did.
    raise RuntimeError("auto_dispatch loop exited without returning or raising")  # pragma: no cover


def record_dispatch_approval(
    repo_root: Path,
    *,
    now: datetime | None = None,
    by: str | None = None,
) -> None:
    """Record one-time auto-mode dispatch approval into ``.forge/config.json``.

    Idempotent: when ``cross_ai.dispatch_approved_at`` is already a
    truthy string the helper returns without touching the file. The
    on-disk bytes stay byte-identical so any file watcher pointed at
    ``config.json`` does not fire spuriously on a re-approval.

    Schema invariant: the written ``cross_ai`` block always carries a
    ``mode`` field — required by ``schemas/cross-ai-config.schema.json``
    and enforced by :func:`tools.cross_ai.config.load_config`. When the
    block is created (no prior ``cross_ai``), ``mode`` is seeded to
    ``"manual"`` so a subsequent ``load_config`` does not blow up with
    ``CrossAiConfigError 'mode' is a required property``. An existing
    ``mode`` value is preserved as-is.

    Args:
        repo_root: Repository root that contains the ``.forge/``
            directory. The config path is computed as
            ``repo_root / ".forge" / "config.json"``.
        now: Injectable UTC clock. ``None`` defaults to
            :func:`datetime.now` (UTC). The value is truncated to
            seconds before being formatted via :meth:`datetime.isoformat`
            so the on-disk literal stays predictable for tests and
            human readers.
        by: Approver identity. ``None`` resolves from the ``USER``
            environment variable, falling back to ``"unknown"`` when
            unset.
    """
    config_path = repo_root / ".forge" / "config.json"

    if config_path.exists():
        document: dict[str, object] = json.loads(config_path.read_text(encoding="utf-8"))
    else:
        document = {}

    cross_ai_block = document.get("cross_ai")
    if not isinstance(cross_ai_block, dict):
        cross_ai_block = {}

    existing = cross_ai_block.get("dispatch_approved_at")
    if isinstance(existing, str) and existing:
        # Idempotent: do not rewrite the file, do not bump mtime.
        return

    effective_now = (now or datetime.now(UTC)).replace(microsecond=0)
    effective_by = by if by is not None else os.environ.get("USER", "unknown")

    # Seed ``mode`` only when absent — preserves an existing
    # ``mode: "auto"`` / ``"disabled"`` setting unchanged.
    if "mode" not in cross_ai_block:
        cross_ai_block["mode"] = "manual"
    cross_ai_block["dispatch_approved_at"] = effective_now.isoformat()
    cross_ai_block["dispatch_approved_by"] = effective_by
    document["cross_ai"] = cross_ai_block

    _atomic_write_text(
        config_path,
        json.dumps(document, indent=2, sort_keys=True),
        prefix=".cross-ai-config-",
    )


def write_response_to_disk(
    response_text: str,
    feature_id: str,
    target: PromptTarget,
    repo_root: Path,
    *,
    now: datetime | None = None,
) -> Path:
    """Persist a captured reviewer response to the cross-AI directory.

    The destination is
    ``<repo_root>/.forge/features/<feature_id>/cross-ai/<target>-<utc>-response.md``.
    Mirrors the :func:`tools.cross_ai.manual.write_prompt_to_disk`
    shape so the prompt and the response sit side-by-side with parallel
    filename conventions; the only delta is the ``-response`` suffix in
    place of ``-prompt``.

    Args:
        response_text: Raw reviewer output. Persisted verbatim — this
            helper does not parse, normalize, or trim.
        feature_id: Folder name under ``.forge/features/``.
        target: ``PromptTarget.plan`` or ``PromptTarget.code`` —
            drives the filename prefix.
        repo_root: Repository root containing ``.forge/``.
        now: Injectable UTC clock. ``None`` defaults to
            :func:`datetime.now` (UTC). Tests pass a fixed value so the
            filename timestamp is deterministic.

    Returns:
        Absolute path to the written file. Callers persist this in
        ``state.json`` so the parser step can locate the response
        without recomputing the timestamp.
    """
    timestamp = (now or datetime.now(UTC)).strftime(_TIMESTAMP_FMT)
    target_dir = repo_root / ".forge" / "features" / feature_id / "cross-ai"
    response_path = target_dir / f"{target.value}-{timestamp}-response.md"
    _atomic_write_text(response_path, response_text, prefix=".cross-ai-response-")
    return response_path.resolve()

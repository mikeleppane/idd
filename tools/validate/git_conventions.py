"""Git-conventions structural validator (commit message shape over state.commits[]).

Reads the feature's ``state.json`` to enumerate commit SHAs, shells out to
``git show -s --format=%B`` for each, then checks the full message against
the configured contract:

* Subject length within ``git_conventions.subject.max_length``.
* Subject grammar matches Conventional Commits when
  ``require_conventional_commits`` is true.
* Subject scope (when present) belongs to ``allowed_scopes`` when that list
  is non-empty. A subject with NO scope is allowed even when the list is set
  — some commit types legitimately omit scope.
* No trailer line in the message matches any configured ``ban_patterns``
  (compared with :func:`re.fullmatch`).

Subprocess errors (missing SHA, no ``git`` binary on PATH, timeout) downgrade
to a ``WARN`` ``unknown-sha:<sha>`` finding and never raise. Config defaults
are applied when ``.forge/config.json`` is missing or lacks the
``git_conventions`` block; a malformed config falls back to defaults so the
parse-error surface stays owned by :func:`tools.validate.validate_config`.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from ._finding import Finding, Severity

_TARGET: Final[str] = "git-conventions"

_DEFAULT_MAX_LENGTH: Final[int] = 72
_DEFAULT_REQUIRE_CC: Final[bool] = True
_DEFAULT_TIMEOUT: Final[float] = 10.0

# Short SHA prefix length surfaced in finding messages — matches `git log
# --abbrev=8` and keeps the operator's eye on a copy-pasteable id.
_SHORT_SHA_LEN: Final[int] = 8

# Trailer excerpt cap for finding messages so long signatures do not blow
# past the readable width of typical terminals.
_TRAILER_EXCERPT_MAX: Final[int] = 80

# Distance from `<feature-folder>` up to the repo root: ``<root>/.forge/features/<id>``
# is three ``parents[]`` entries deep, so the root sits at ``parents[2]``.
_FEATURE_DEPTH: Final[int] = 3

# Conventional Commits grammar. Allows optional scope, optional `!` breaking
# marker, and a non-empty description starting with a non-space character.
_CC_SUBJECT = re.compile(
    r"^(?P<type>feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert)"
    r"(?:\((?P<scope>[a-z][a-z0-9-]*)\))?!?: (?P<desc>\S.*)$",
)

# A trailer line: ``Key: value`` where Key is RFC-5322-ish (letters + hyphens,
# starting with a letter). Single-line values only — multi-line continuation
# is treated as body, not a trailer.
_TRAILER_LINE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*: .+$")

_SEVERITY_RANK: dict[Severity, int] = {
    "BLOCK": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
    "WARN": 4,
    "INFO": 5,
}


@dataclass(frozen=True, kw_only=True)
class GitConventionsConfig:
    """Effective git-conventions config (explicit settings + applied defaults).

    Attributes:
        subject_max_length: Inclusive upper bound for the commit subject in
            characters. Defaults to 72.
        require_conventional_commits: When True, subjects must match the
            Conventional Commits grammar (``<type>(<scope>)!: <desc>``).
        allowed_scopes: Permitted scope tokens. Empty tuple disables the
            scope-allowlist check entirely. A commit with no scope is
            always allowed regardless of this list.
        trailer_ban_patterns: Regex patterns matched against each trailer
            line via :func:`re.fullmatch`. A hit emits a ``BLOCK`` finding.
    """

    subject_max_length: int
    require_conventional_commits: bool
    allowed_scopes: tuple[str, ...]
    trailer_ban_patterns: tuple[str, ...]


GitRunner = Callable[..., subprocess.CompletedProcess[str]]


def _default_runner(
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
) -> subprocess.CompletedProcess[str]:
    """Production runner: ``subprocess.run`` with safe, locked-down defaults."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        cwd=cwd,
    )


def _read_config_block(repo_root: Path) -> dict[str, Any]:
    """Read ``.forge/config.json`` and return the ``git_conventions`` sub-block.

    Returns an empty dict when the file is missing, the JSON fails to parse,
    or the block is absent. Parse-error reporting belongs to
    :func:`tools.validate.validate_config`; this loader fails open so a busted
    config does not block the validator from running with defaults.
    """
    path = repo_root / ".forge" / "config.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    block = payload.get("git_conventions")
    if not isinstance(block, dict):
        return {}
    return block


def load_config(repo_root: Path) -> GitConventionsConfig:
    """Load the effective git-conventions config for ``repo_root``.

    Missing config file, missing ``git_conventions`` block, malformed JSON,
    and partial sub-blocks all resolve to documented defaults. The returned
    dataclass is frozen so downstream callers cannot mutate the effective
    contract by accident.

    Args:
        repo_root: Repository root containing the ``.forge`` directory.

    Returns:
        :class:`GitConventionsConfig` with defaults applied.
    """
    block = _read_config_block(repo_root)
    subject_block = block.get("subject")
    subject: dict[str, Any] = subject_block if isinstance(subject_block, dict) else {}
    trailers_block = block.get("trailers")
    trailers: dict[str, Any] = trailers_block if isinstance(trailers_block, dict) else {}

    max_length_raw = subject.get("max_length")
    max_length = (
        max_length_raw
        if isinstance(max_length_raw, int) and not isinstance(max_length_raw, bool)
        else _DEFAULT_MAX_LENGTH
    )

    require_cc_raw = subject.get("require_conventional_commits")
    require_cc = require_cc_raw if isinstance(require_cc_raw, bool) else _DEFAULT_REQUIRE_CC

    scopes_raw = subject.get("allowed_scopes")
    if isinstance(scopes_raw, list):
        allowed_scopes = tuple(s for s in scopes_raw if isinstance(s, str))
    else:
        allowed_scopes = ()

    bans_raw = trailers.get("ban_patterns")
    if isinstance(bans_raw, list):
        ban_patterns = tuple(p for p in bans_raw if isinstance(p, str))
    else:
        ban_patterns = ()

    return GitConventionsConfig(
        subject_max_length=max_length,
        require_conventional_commits=require_cc,
        allowed_scopes=allowed_scopes,
        trailer_ban_patterns=ban_patterns,
    )


def _short(sha: str) -> str:
    return sha[:_SHORT_SHA_LEN] if len(sha) >= _SHORT_SHA_LEN else sha


def _split_message(message: str) -> tuple[str, list[str]]:
    """Return ``(subject, trailer_lines)`` for a git commit message.

    The trailer block is the contiguous tail of ``Key: value`` lines that
    follow the last blank line in the message. Single-line values only —
    a folded continuation (RFC 5322) is left in the body because folded
    trailers are rare in git history and conservative parsing avoids false
    positives.

    Trailing blank lines are ignored. A message with no blank line after the
    subject has no body and no trailers; the result is ``(subject, [])``.
    """
    # Normalise line endings and strip trailing blank padding so the
    # "tail after last blank line" calculation stays stable.
    lines = message.rstrip("\n").split("\n")
    if not lines:
        return "", []
    subject = lines[0]
    rest = lines[1:]
    # Find the index AFTER the last blank line in `rest`. Everything from
    # there to the end is the candidate trailer block.
    last_blank = -1
    for idx, line in enumerate(rest):
        if line == "":
            last_blank = idx
    if last_blank == -1:
        return subject, []
    candidate = rest[last_blank + 1 :]
    if not candidate:
        return subject, []
    # All candidate lines must look like trailers; otherwise treat the block
    # as body (mixed Key:value + free-form text is not a trailer block).
    for line in candidate:
        if not _TRAILER_LINE.match(line):
            return subject, []
    return subject, candidate


def _check_subject(
    *,
    sha: str,
    subject: str,
    config: GitConventionsConfig,
    state_path: Path,
) -> list[Finding]:
    findings: list[Finding] = []
    if len(subject) > config.subject_max_length:
        findings.append(
            Finding(
                "HIGH",
                _TARGET,
                state_path,
                (
                    f"{_short(sha)}: subject exceeds {config.subject_max_length} chars "
                    f"(got {len(subject)})"
                ),
            ),
        )

    if not config.require_conventional_commits:
        return findings

    match = _CC_SUBJECT.match(subject)
    if match is None:
        findings.append(
            Finding(
                "HIGH",
                _TARGET,
                state_path,
                f"{_short(sha)}: subject does not match Conventional Commits format",
            ),
        )
        return findings

    scope = match.group("scope")
    if scope is not None and config.allowed_scopes and scope not in config.allowed_scopes:
        findings.append(
            Finding(
                "HIGH",
                _TARGET,
                state_path,
                f"{_short(sha)}: scope {scope!r} not in allowed_scopes",
            ),
        )
    return findings


def _check_trailers(
    *,
    sha: str,
    trailers: list[str],
    config: GitConventionsConfig,
    state_path: Path,
) -> list[Finding]:
    findings: list[Finding] = []
    for pattern in config.trailer_ban_patterns:
        try:
            compiled = re.compile(pattern)
        except re.error:
            findings.append(
                Finding(
                    "BLOCK",
                    _TARGET,
                    state_path,
                    f"trailer ban_pattern failed to compile: {pattern!r}",
                ),
            )
            continue
        for trailer in trailers:
            if compiled.fullmatch(trailer):
                excerpt = (
                    trailer
                    if len(trailer) <= _TRAILER_EXCERPT_MAX
                    else trailer[:_TRAILER_EXCERPT_MAX]
                )
                findings.append(
                    Finding(
                        "BLOCK",
                        _TARGET,
                        state_path,
                        (
                            f"{_short(sha)}: forbidden trailer matched pattern "
                            f"{pattern!r}: {excerpt}"
                        ),
                    ),
                )
    return findings


def _fetch_message(
    runner: GitRunner,
    sha: str,
    *,
    cwd: Path,
) -> str | None:
    """Return the commit message body, or ``None`` if the SHA is unreachable."""
    try:
        verify = runner(
            ["git", "rev-parse", "--verify", f"{sha}^{{commit}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_DEFAULT_TIMEOUT,
            cwd=cwd,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if verify.returncode != 0:
        return None
    try:
        show = runner(
            ["git", "show", "-s", "--format=%B", sha],
            capture_output=True,
            text=True,
            check=False,
            timeout=_DEFAULT_TIMEOUT,
            cwd=cwd,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if show.returncode != 0:
        return None
    return show.stdout


def _check_commit(
    *,
    sha: str,
    config: GitConventionsConfig,
    runner: GitRunner,
    state_path: Path,
    cwd: Path,
) -> list[Finding]:
    message = _fetch_message(runner, sha, cwd=cwd)
    if message is None:
        return [Finding("WARN", _TARGET, state_path, f"unknown-sha:{sha}")]
    subject, trailers = _split_message(message)
    findings: list[Finding] = []
    findings.extend(_check_subject(sha=sha, subject=subject, config=config, state_path=state_path))
    findings.extend(
        _check_trailers(sha=sha, trailers=trailers, config=config, state_path=state_path)
    )
    return findings


def _load_commits(state_path: Path) -> list[dict[str, Any]] | Finding:
    """Return ``state.commits[]`` or a single ``BLOCK`` Finding on load failure.

    A missing ``commits`` field, or a value that is not a list, resolves to an
    empty list — the validator silently passes for features that predate any
    commits or that store the field shape unexpectedly. The structural shape
    of ``state.json`` itself is owned by :mod:`tools.validate.health`.
    """
    if not state_path.is_file():
        return Finding("BLOCK", _TARGET, state_path, "state.json not found")
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return Finding("BLOCK", _TARGET, state_path, f"failed to parse state.json: {exc}")
    if not isinstance(payload, dict):
        return Finding("BLOCK", _TARGET, state_path, "state.json root must be an object")
    commits = payload.get("commits")
    if not isinstance(commits, list):
        return []
    return [c for c in commits if isinstance(c, dict)]


def _sort_key(commit_index: int, finding: Finding) -> tuple[int, int, str]:
    return commit_index, _SEVERITY_RANK.get(finding.severity, 99), finding.message


def validate_git_conventions(
    feature_folder: Path,
    *,
    runner: GitRunner | None = None,
) -> list[Finding]:
    """Validate every commit recorded in ``state.commits[]`` for a feature.

    Args:
        feature_folder: Path to ``.forge/features/<feature-id>/``. Must contain
            ``state.json``; missing or malformed state surfaces a ``BLOCK``
            finding pointing at the expected file.
        runner: Injection seam for ``subprocess.run``. Defaults to the
            production runner with a 10 s timeout, ``capture_output=True``,
            ``text=True``, and ``check=False``.

    Returns:
        Findings in deterministic order: by commit index, then severity rank,
        then message. ``WARN`` findings name the unreachable SHA in
        ``unknown-sha:<sha>`` form so downstream consumers can dedupe.
    """
    effective_runner: GitRunner = runner if runner is not None else _default_runner
    state_path = feature_folder / "state.json"
    loaded = _load_commits(state_path)
    if isinstance(loaded, Finding):
        return [loaded]

    # Repo root sits at .forge/features/<id>/ → ``parents[2]``.
    cwd = (
        feature_folder.parents[_FEATURE_DEPTH - 1]
        if len(feature_folder.parents) >= _FEATURE_DEPTH
        else feature_folder
    )
    config = load_config(cwd)

    indexed: list[tuple[int, Finding]] = []
    for idx, commit in enumerate(loaded):
        sha_raw = commit.get("sha")
        if not isinstance(sha_raw, str) or not sha_raw:
            continue
        commit_findings = _check_commit(
            sha=sha_raw,
            config=config,
            runner=effective_runner,
            state_path=state_path,
            cwd=cwd,
        )
        indexed.extend((idx, finding) for finding in commit_findings)

    indexed.sort(key=lambda pair: _sort_key(pair[0], pair[1]))
    return [finding for _, finding in indexed]

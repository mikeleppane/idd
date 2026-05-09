"""TDD evidence validator: enforce paired test→impl commits per acceptance criterion.

For every acceptance criterion implemented during the execute phase, the
matching impl (or refactor-touching-production) commit must be preceded by
a test commit recorded earlier in ``state.commits[]``. Pairing is determined
by commit insertion order in ``state.commits[]`` — the canonical chronology
maintained by ``tools.state.append_commit`` — not by the second-precision
``logged_at`` timestamp (which collides for fast-batched commits).

The validator is a pure function. Diff inspection is delegated to an
injectable ``git_show_files`` callable so tests can fake commits without
shelling out, and so production callers can swap in alternate inspectors
(e.g. a libgit2 wrapper) without touching this module.

Findings (severity → code → meaning):

- ``BLOCK`` ``tdd_evidence:feature_missing``: feature directory absent.
- ``BLOCK`` ``tdd_evidence:missing_test_pair``: AC has impl commit(s) but no
  test commit recorded strictly earlier in ``state.commits[]``, and no
  ``## TDD Exception: <ac-id>`` ADR in ``decisions.md``.
- ``BLOCK`` ``tdd_evidence:refactor_unpaired``: AC has a refactor commit
  touching production paths (``src/`` / ``tools/`` / ``hooks/`` / ``schemas/``)
  without a paired preceding test commit and no TDD Exception ADR.
- ``BLOCK`` ``tdd_evidence:ac_unmapped_to_slice``: AC declared in SPEC.md
  has no SHA recorded in any ``slice-*.summary`` while execute-phase commits
  exist; means the slice summary was not authored or under-populated.
- ``BLOCK`` ``tdd_evidence:orphan_commit_no_slice``: an execute-phase commit
  in ``state.commits[]`` is referenced by no slice summary, so the gate
  cannot map it to an AC.
- ``BLOCK`` ``tdd_evidence:exception_keys_missing``: a ``## TDD Exception``
  ADR section is missing one or more required keys (``Rationale``,
  ``Reviewer``, ``Date``) or has an empty value for one.
- ``LOW`` ``tdd_evidence:suspicious_test_commit``: test commit's diff touches
  paths outside ``tests/``.
- ``INFO`` ``tdd_evidence:no_impl_commits``: AC has only docs / chore
  commits, no impl. Advisory — might mean the slice misclassified its scope.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from ._feature_layout import DECISIONS_FILENAME, SPEC_FILENAME, STATE_FILENAME
from ._finding import Finding, Severity
from ._frontmatter import _read_text

TARGET = "tdd_evidence"

_AC_TAG_RE = re.compile(r"\b(?:AC-(\d+)|crit-(\d+))\b", re.IGNORECASE)
_AC_LINE_RE = re.compile(r"^(?P<ac>AC-\d+|crit-\d+)\s*:\s*(?P<sha>[0-9a-f]{7,40})", re.MULTILINE)
_SHA_NEAR_AC_RE = re.compile(r"\b([0-9a-f]{7,40})\b")
_AC_NUMBERED_RE = re.compile(r"^(\d+)\.\s+\S+", re.MULTILINE)
_ACCEPTANCE_BLOCK_RE = re.compile(r"(?ms)^# Acceptance Criteria\b[^\n]*\n(?P<body>.*?)(?=^# |\Z)")
_TDD_EXCEPTION_HEADING_RE = re.compile(
    r"^##\s+TDD\s+Exception:\s+(?P<ac>AC-\d+|crit-\d+)\s*$",
    re.MULTILINE,
)
_REQUIRED_EXCEPTION_KEYS: tuple[str, ...] = ("Rationale", "Reviewer", "Date")

_SEVERITY_RANK: dict[Severity, int] = {
    "BLOCK": 0,
    "HIGH": 1,
    "MEDIUM": 2,
    "LOW": 3,
    "WARN": 4,
    "INFO": 5,
}

_PRODUCTION_PREFIXES: tuple[str, ...] = ("src/", "tools/", "hooks/", "schemas/")

_AC_ID_IN_MESSAGE_RE = re.compile(r"AC-\d+")
_CODE_PREFIX = "tdd_evidence:"


def _real_git_show_files(sha: str) -> list[str]:
    """Default ``git_show_files`` impl — shells out to ``git show --name-only``."""
    proc = subprocess.run(
        ["git", "show", "--name-only", "--pretty=format:", sha],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _normalise_ac_id(raw: str) -> str:
    """Return canonical ``AC-<n>`` form for matching across heterogeneous sources."""
    match = re.match(r"(?:AC|crit)-(\d+)", raw, re.IGNORECASE)
    if match is None:
        return raw
    return f"AC-{match.group(1)}"


_ROLE_BY_PREFIX: dict[str, str] = {
    "test": "test",
    "feat": "impl",
    "fix": "impl",
    "refactor": "refactor",
    "docs": "docs",
    "chore": "chore",
}


def _classify_subject(subject: str) -> str:
    """Map a Conventional-Commit subject to a role.

    Returns one of: ``test`` / ``impl`` / ``refactor`` / ``docs`` / ``chore``
    / ``other``. The leading type token is taken from the subject up to the
    first ``:``, ``(``, or whitespace, with any trailing ``!`` (Conventional
    Commits breaking-change marker) stripped so ``feat!: msg`` still
    classifies as impl.
    """
    head = subject.strip().split(":", 1)[0].lower()
    prefix = re.split(r"[\s(]", head, maxsplit=1)[0].rstrip("!")
    return _ROLE_BY_PREFIX.get(prefix, "other")


def _extract_ac_ids_from_spec(spec_text: str) -> list[str]:
    block = _ACCEPTANCE_BLOCK_RE.search(spec_text)
    if block is None:
        return []
    return [f"AC-{m.group(1)}" for m in _AC_NUMBERED_RE.finditer(block.group("body"))]


def _parse_slice_summaries(feature_dir: Path) -> dict[str, set[str]]:
    """Walk ``slice-*.summary`` files; return AC -> set of commit SHAs.

    Two parsing strategies are supported (both forgiving):

    1. Explicit ``AC-<n>: <sha>`` lines.
    2. Lines containing both an AC tag and a SHA in any order — useful when
       authors record ``feat(...): AC-1 implemented in abcdef0`` style.
    """
    out: dict[str, set[str]] = {}
    for summary_path in sorted(feature_dir.glob("slice-*.summary")):
        text = _read_text(summary_path)
        if text is None:
            continue
        for match in _AC_LINE_RE.finditer(text):
            ac_id = _normalise_ac_id(match.group("ac"))
            out.setdefault(ac_id, set()).add(match.group("sha"))
        for line in text.splitlines():
            ac_match = _AC_TAG_RE.search(line)
            sha_match = _SHA_NEAR_AC_RE.search(line)
            if ac_match is None or sha_match is None:
                continue
            digits = ac_match.group(1) or ac_match.group(2)
            ac_id = f"AC-{digits}"
            out.setdefault(ac_id, set()).add(sha_match.group(1))
    return out


def _parse_tdd_exception_sections(decisions_text: str) -> dict[str, dict[str, str]]:
    """Return ``{ac_id: {key: value}}`` for each ``## TDD Exception`` section.

    Each section spans from its ``## TDD Exception: <ac-id>`` heading up to
    the next ``^## `` heading or end of file. Within the section, lines of
    the form ``- Key: value``, ``Key: value``, or ``**Key:** value`` are
    parsed into the key/value map. Whitespace and surrounding markdown
    emphasis are stripped from the key.
    """
    out: dict[str, dict[str, str]] = {}
    matches = list(_TDD_EXCEPTION_HEADING_RE.finditer(decisions_text))
    for idx, match in enumerate(matches):
        ac_id = _normalise_ac_id(match.group("ac"))
        body_start = match.end()
        body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(decisions_text)
        body = decisions_text[body_start:body_end]
        # Stop at the next H2 even if it's not a TDD Exception heading.
        cutoff = re.search(r"(?m)^## ", body)
        if cutoff is not None:
            body = body[: cutoff.start()]
        keys = _parse_kv_block(body)
        out[ac_id] = keys
    return out


def _parse_kv_block(body: str) -> dict[str, str]:
    """Extract ``Key: value`` pairs from an ADR section body.

    Accepts ``Key: value``, ``- Key: value``, ``**Key:** value``, and
    leading-bullet variants. Multi-line values continue until the next
    key-line or blank line; only the first physical line is captured (the
    validator only checks key presence and non-emptiness).
    """
    out: dict[str, str] = {}
    line_re = re.compile(
        r"^\s*(?:-\s+|\*\s+)?(?:\*\*)?(?P<key>[A-Za-z][A-Za-z _-]*?)(?:\*\*)?\s*:\s*(?P<value>.*?)\s*$",
    )
    for raw in body.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        m = line_re.match(line)
        if m is None:
            continue
        key = m.group("key").strip()
        value = m.group("value").strip().lstrip("*").strip()
        if key and key not in out:
            out[key] = value
    return out


def _exception_finding(
    ac_id: str,
    section: dict[str, str],
    state_path: Path,
) -> Finding | None:
    """BLOCK when an exception ADR section is missing required keys or values."""
    missing = [k for k in _REQUIRED_EXCEPTION_KEYS if not section.get(k, "").strip()]
    if not missing:
        return None
    return Finding(
        "BLOCK",
        TARGET,
        state_path,
        f"tdd_evidence:exception_keys_missing — {ac_id} TDD Exception ADR is "
        f"missing or has empty values for: {sorted(missing)}",
    )


def _load_execute_commits(state_path: Path) -> list[dict[str, str]]:
    """Return execute-phase commits in ``state.commits[]`` insertion order.

    Each entry carries ``sha``, ``subject``, ``logged_at`` (kept for display
    only), and ``commit_index`` — the position in the original list, used as
    the canonical chronology for pairing decisions. Insertion order is
    authoritative because ``logged_at`` has second precision and collides
    when commits are batched in the same wall second.
    """
    text = _read_text(state_path)
    if text is None:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return []
    commits = payload.get("commits") if isinstance(payload, dict) else None
    if not isinstance(commits, list):
        return []
    out: list[dict[str, str]] = []
    for idx, entry in enumerate(commits):
        if not isinstance(entry, dict):
            continue
        if entry.get("phase") != "execute":
            continue
        sha = str(entry.get("sha", ""))
        subject = str(entry.get("subject", ""))
        logged_at = str(entry.get("logged_at", ""))
        if not sha or not subject:
            continue
        out.append(
            {
                "sha": sha,
                "subject": subject,
                "logged_at": logged_at,
                "commit_index": str(idx),
            }
        )
    return out


def _finding_sort_key(f: Finding) -> tuple[int, str, str, str]:
    """Sort by (severity, message-code, AC id, full message) for stable output.

    The message-code prefix is the substring after ``tdd_evidence:`` up to
    the first ``—``; the AC id is the first ``AC-<n>`` substring in the
    message (empty string when absent). Falling back to the full message at
    the end keeps tied keys deterministic.
    """
    code = ""
    body = f.message
    if body.startswith(_CODE_PREFIX):
        rest = body[len(_CODE_PREFIX) :]
        code = rest.split(" ", 1)[0].rstrip(":—-")
    ac_match = _AC_ID_IN_MESSAGE_RE.search(body)
    ac = ac_match.group(0) if ac_match else ""
    return (_SEVERITY_RANK.get(f.severity, 99), code, ac, body)


def _sort_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=_finding_sort_key)


def _shas_match(declared: str, candidate: str) -> bool:
    """Match short and long SHAs against each other prefix-wise."""
    if not declared or not candidate:
        return False
    if declared == candidate:
        return True
    short, long = sorted((declared, candidate), key=len)
    return long.startswith(short)


def _commits_for_ac(
    ac_shas: set[str],
    execute_commits: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Filter execute_commits to those whose SHA matches one of ac_shas."""
    return [c for c in execute_commits if any(_shas_match(s, c["sha"]) for s in ac_shas)]


def _classify_ac_commits(
    ac_commits: list[dict[str, str]],
) -> dict[str, list[dict[str, str]]]:
    roles: dict[str, list[dict[str, str]]] = {
        "test": [],
        "impl": [],
        "refactor": [],
        "docs": [],
        "chore": [],
        "other": [],
    }
    for commit in ac_commits:
        roles[_classify_subject(commit["subject"])].append(commit)
    return roles


def _diff_findings_for_ac(
    ac_id: str,
    roles: dict[str, list[dict[str, str]]],
    inspect: Callable[[str], list[str]],
    state_path: Path,
) -> list[Finding]:
    """Emit advisory ``LOW`` findings for test commits that touch non-test paths.

    The refactor-touches-production check is now load-bearing for the
    pairing rule and lives in ``_pairing_findings_for_ac``; this helper is
    advisory-only.
    """
    out: list[Finding] = []
    for test_commit in roles["test"]:
        files = inspect(test_commit["sha"])
        non_test = [f for f in files if not f.startswith("tests/")]
        if files and non_test:
            out.append(
                Finding(
                    "LOW",
                    TARGET,
                    state_path,
                    f"tdd_evidence:suspicious_test_commit — {ac_id} test commit "
                    f"{test_commit['sha']} touches non-test paths: {sorted(non_test)}",
                )
            )
    return out


def _earliest_index(commits: list[dict[str, str]]) -> int | None:
    """Return the smallest ``commit_index`` across the given commits, or None."""
    indices = [int(c["commit_index"]) for c in commits if c.get("commit_index")]
    return min(indices) if indices else None


def _has_preceding_test(roles: dict[str, list[dict[str, str]]], pivot_index: int) -> bool:
    """True iff any ``test`` commit precedes the pivot in ``state.commits[]`` order."""
    for test_commit in roles["test"]:
        idx = test_commit.get("commit_index")
        if idx is not None and int(idx) < pivot_index:
            return True
    return False


def _pairing_findings_for_ac(
    ac_id: str,
    roles: dict[str, list[dict[str, str]]],
    inspect: Callable[[str], list[str]],
    state_path: Path,
) -> list[Finding]:
    """Emit BLOCK / INFO findings driven by the test/impl pairing rule.

    Pairing rules:

    - An impl commit (``feat`` / ``fix``) always requires a paired test
      commit recorded earlier in ``state.commits[]``.
    - A refactor commit that touches a production path
      (``src/`` / ``tools/`` / ``hooks/`` / ``schemas/``) is treated as
      impl-equivalent and requires the same paired test. Refactor commits
      that touch only non-production paths (e.g. tests, docs) skip the
      pairing requirement.
    - Pairing is decided by ``state.commits[]`` insertion order (the
      ``commit_index`` field), not by ``logged_at`` strings, so commits
      batched in the same wall second still pair correctly.
    """
    impl_commits = list(roles["impl"])
    refactor_in_prod = [
        c
        for c in roles["refactor"]
        if any(f.startswith(_PRODUCTION_PREFIXES) for f in inspect(c["sha"]))
    ]

    pivot_commits = impl_commits + refactor_in_prod
    if not pivot_commits:
        if not (roles["test"] or roles["refactor"] or roles["docs"] or roles["chore"]):
            return []
        return [
            Finding(
                "INFO",
                TARGET,
                state_path,
                f"tdd_evidence:no_impl_commits — {ac_id} has only "
                f"non-production commits; verify scope classification",
            )
        ]

    earliest_pivot = _earliest_index(pivot_commits)
    if earliest_pivot is None:
        return []
    if _has_preceding_test(roles, earliest_pivot):
        return []

    if impl_commits:
        return [
            Finding(
                "BLOCK",
                TARGET,
                state_path,
                f"tdd_evidence:missing_test_pair — {ac_id} has impl commit "
                f"without a preceding test commit (and no TDD Exception ADR)",
            )
        ]
    return [
        Finding(
            "BLOCK",
            TARGET,
            state_path,
            f"tdd_evidence:refactor_unpaired — {ac_id} has refactor commit "
            f"touching production paths without a preceding test commit "
            f"(and no TDD Exception ADR)",
        )
    ]


def _orphan_commit_findings(
    execute_commits: list[dict[str, str]],
    slice_map: dict[str, set[str]],
    state_path: Path,
) -> list[Finding]:
    """BLOCK on each execute commit not referenced by any slice summary.

    An orphan commit cannot be mapped to an AC, which means the gate cannot
    decide whether it is paired with a test. Refactor / chore / docs / other
    commits are exempt because they do not need to map to an AC by
    convention; only test / impl commits surface here.
    """
    declared: set[str] = set()
    for shas in slice_map.values():
        declared.update(shas)

    out: list[Finding] = []
    for commit in execute_commits:
        role = _classify_subject(commit["subject"])
        if role not in {"test", "impl"}:
            continue
        sha = commit["sha"]
        if any(_shas_match(d, sha) for d in declared):
            continue
        out.append(
            Finding(
                "BLOCK",
                TARGET,
                state_path,
                f"tdd_evidence:orphan_commit_no_slice — execute commit {sha} "
                f"({commit['subject']!r}) is not referenced by any slice-*.summary",
            )
        )
    return out


def validate_tdd_evidence(
    repo_root: Path,
    feature_id: str,
    *,
    git_show_files: Callable[[str], list[str]] | None = None,
) -> list[Finding]:
    """Assert every implemented acceptance criterion has a paired preceding test commit.

    Args:
        repo_root: Repository root containing ``.forge/features/<feature_id>/``.
        feature_id: Slug folder name under ``.forge/features``.
        git_show_files: Callable that returns the file paths touched by a
            commit, used for diff-shape inspection. Defaults to a
            ``git show --name-only`` shell-out; tests inject a fake.

    Returns:
        Sorted list of Finding records. Empty list means the feature's
        execute-phase commits satisfy the paired-commit rule.
    """
    inspect = git_show_files if git_show_files is not None else _real_git_show_files

    feature_dir = repo_root / ".forge" / "features" / feature_id
    if not feature_dir.is_dir():
        return [
            Finding(
                "BLOCK",
                TARGET,
                feature_dir,
                f"tdd_evidence:feature_missing — {feature_dir} does not exist",
            )
        ]

    spec_text = _read_text(feature_dir / SPEC_FILENAME) or ""
    decisions_text = _read_text(feature_dir / DECISIONS_FILENAME) or ""
    exception_sections = _parse_tdd_exception_sections(decisions_text)

    ac_ids = _extract_ac_ids_from_spec(spec_text)
    slice_map = _parse_slice_summaries(feature_dir)
    for slice_ac in slice_map:
        if slice_ac not in ac_ids:
            ac_ids.append(slice_ac)

    execute_commits = _load_execute_commits(feature_dir / STATE_FILENAME)
    state_path = feature_dir / STATE_FILENAME

    findings: list[Finding] = []
    findings.extend(_orphan_commit_findings(execute_commits, slice_map, state_path))

    for ac_id in ac_ids:
        ac_shas = slice_map.get(ac_id, set())
        if not ac_shas and execute_commits:
            findings.append(
                Finding(
                    "BLOCK",
                    TARGET,
                    state_path,
                    f"tdd_evidence:ac_unmapped_to_slice — {ac_id} is declared in "
                    f"SPEC.md but no slice-*.summary records a commit for it",
                )
            )
            continue
        roles = _classify_ac_commits(_commits_for_ac(ac_shas, execute_commits))
        findings.extend(_diff_findings_for_ac(ac_id, roles, inspect, state_path))

        if ac_id in exception_sections:
            key_finding = _exception_finding(ac_id, exception_sections[ac_id], state_path)
            if key_finding is not None:
                findings.append(key_finding)
            continue

        findings.extend(_pairing_findings_for_ac(ac_id, roles, inspect, state_path))

    return _sort_findings(findings)


__all__ = ["TARGET", "validate_tdd_evidence"]

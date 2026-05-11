"""CLI entry point for /forge:validate (M3 §5.3.6 D-CLI).

Wires structural (P2a) and semantic (P2b) validators behind the
``--target`` flag. ``--target all`` fans out across the full ``.forge``
tree: it runs ``validate_health`` + ``validate_capability_uniqueness``
once at the repo level, then walks ``.forge/changes/`` and ``.forge/features/``
applying the appropriate per-artifact validators.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from ._config_shape import validate_config
from ._feature_layout import PLAN_FILENAME, SPEC_FILENAME
from ._finding import EXIT_NONZERO_SEVERITIES, Finding, _finding_to_dict
from ._research_shape import validate_research
from ._review_lesson_tags import validate_review_md_lesson_tags
from .constitution import validate_constitution
from .conventions import validate_conventions
from .delta import validate_delta
from .domain_glossary import validate_domain_glossary
from .git_conventions import validate_git_conventions
from .health import validate_health
from .lessons import validate_lessons
from .plan import validate_plan_tasks, validate_verified_deps
from .qa_shape import validate_qa_shape
from .spec_semantic import validate_anchors, validate_scenarios
from .spec_structural import (
    validate_capability_uniqueness,
    validate_frontmatter,
    validate_negative_requirements,
)
from .state_semantic import validate_deviations
from .tdd_evidence import validate_tdd_evidence

_PER_FILE_TARGETS: frozenset[str] = frozenset(
    {
        "spec",
        "plan",
        "delta",
        "scenarios",
        "anchors",
        "spec-semantic",
        "plan-tasks",
        "verified-deps",
        "research",
    }
)
_PER_FOLDER_TARGETS: frozenset[str] = frozenset(
    {"deviations", "tdd_evidence", "domain_glossary", "qa_shape", "git-conventions"}
)
_REPO_WIDE_TARGETS: frozenset[str] = frozenset(
    {"health", "ship", "all", "config", "conventions", "lessons"}
)

# Reserved sub-folder names under ``.forge/features/`` and ``.forge/changes/``
# that the ``--target all`` dispatcher must skip — they are not live artifacts.
_RESERVED_SUBFOLDERS: frozenset[str] = frozenset({"archive"})

_TARGET_CHOICES: tuple[str, ...] = (
    "spec",
    "plan",
    "delta",
    "scenarios",
    "anchors",
    "spec-semantic",
    "plan-tasks",
    "verified-deps",
    "deviations",
    "tdd_evidence",
    "domain_glossary",
    "qa_shape",
    "research",
    "constitution",
    "config",
    "conventions",
    "git-conventions",
    "health",
    "lessons",
    "ship",
    "all",
)


# Dispatch helpers share a uniform `(args, repo_root)` signature so the
# `_TARGET_DISPATCH` table below can reference them all by the same callable
# type. Some helpers do not consume both inputs; ARG001 is silenced
# per-function rather than refactoring away the table.


def _dispatch_spec(args: argparse.Namespace, repo_root: Path) -> list[Finding]:  # noqa: ARG001
    findings: list[Finding] = []
    findings.extend(validate_negative_requirements(args.path))
    findings.extend(validate_frontmatter(args.path, kind="spec"))
    return findings


def _dispatch_plan(args: argparse.Namespace, repo_root: Path) -> list[Finding]:  # noqa: ARG001
    return list(validate_frontmatter(args.path, kind="plan"))


def _dispatch_delta(args: argparse.Namespace, repo_root: Path) -> list[Finding]:  # noqa: ARG001
    return list(validate_delta(args.path))


def _dispatch_scenarios(args: argparse.Namespace, repo_root: Path) -> list[Finding]:  # noqa: ARG001
    return list(validate_scenarios(args.path))


def _dispatch_anchors(args: argparse.Namespace, repo_root: Path) -> list[Finding]:
    return list(validate_anchors(args.path, repo_root=repo_root))


def _dispatch_spec_semantic(args: argparse.Namespace, repo_root: Path) -> list[Finding]:
    findings: list[Finding] = []
    findings.extend(validate_scenarios(args.path))
    findings.extend(validate_anchors(args.path, repo_root=repo_root))
    return findings


def _dispatch_plan_tasks(args: argparse.Namespace, repo_root: Path) -> list[Finding]:  # noqa: ARG001
    spec = args.path.parent / SPEC_FILENAME
    return list(validate_plan_tasks(args.path, spec_path=spec))


def _dispatch_verified_deps(
    args: argparse.Namespace,
    repo_root: Path,  # noqa: ARG001
) -> list[Finding]:
    return list(validate_verified_deps(args.path, check_registries=args.check_registries))


def _dispatch_deviations(args: argparse.Namespace, repo_root: Path) -> list[Finding]:  # noqa: ARG001
    return list(validate_deviations(args.path))


def _dispatch_tdd_evidence(args: argparse.Namespace, repo_root: Path) -> list[Finding]:
    return list(validate_tdd_evidence(repo_root, args.path.name))


def _dispatch_domain_glossary(args: argparse.Namespace, repo_root: Path) -> list[Finding]:
    return list(validate_domain_glossary(repo_root, args.path.name))


def _dispatch_qa_shape(args: argparse.Namespace, repo_root: Path) -> list[Finding]:
    return list(validate_qa_shape(repo_root, args.path.name))


def _dispatch_git_conventions(
    args: argparse.Namespace,
    repo_root: Path,  # noqa: ARG001
) -> list[Finding]:
    return list(validate_git_conventions(args.path))


def _dispatch_research(args: argparse.Namespace, repo_root: Path) -> list[Finding]:  # noqa: ARG001
    return list(validate_research(args.path))


def _dispatch_config(
    args: argparse.Namespace,  # noqa: ARG001
    repo_root: Path,
) -> list[Finding]:
    return list(validate_config(repo_root / ".forge" / "config.json"))


def _dispatch_constitution(args: argparse.Namespace, repo_root: Path) -> list[Finding]:
    resolved = args.path if args.path is not None else repo_root / ".forge" / "CONSTITUTION.md"
    return list(validate_constitution(resolved))


# Subprocess defaults mirror :mod:`tools.validate.git_conventions` — pinned
# timeout, captured streams, non-raising exit handling so a missing SHA does
# not crash the validator on the user.
_GIT_TIMEOUT_SECONDS: float = 10.0


def _read_commit_body(sha: str, repo_root: Path) -> tuple[str | None, Finding | None]:
    """Resolve a commit SHA to its message body via ``git show -s --format=%B``.

    Returns ``(body, None)`` on success, ``(None, WARN-finding)`` on failure.
    Failure modes covered: no ``git`` binary on PATH, missing SHA, subprocess
    timeout, non-zero exit. The CLI must keep validating other rules even
    when the commit lookup fails.

    Implementation: verify the SHA via ``git rev-parse --verify`` first,
    then fetch the body via ``git show -s --format=%B``. Both invocations
    deliberately omit the ``--`` end-of-options separator — for ``git
    rev-parse`` the separator turns the argument into a pathspec and
    triggers ``Needed a single revision``; for ``git show`` it forces the
    SHA to be interpreted as a path so the message body comes back empty.
    Safety against leading-dash positionals comes from the upstream caller
    (CI / scripts) and from ``rev-parse --verify`` rejecting unknown shapes
    natively before we ever reach ``git show``.
    """
    try:
        verify = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--verify", f"{sha}^{{commit}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return None, Finding(
            "WARN",
            "conventions",
            repo_root,
            f"commit lookup failed for {sha!r}: {exc}",
        )
    if verify.returncode != 0:
        err = (verify.stderr or "").strip().splitlines()
        detail = err[0] if err else f"exit {verify.returncode}"
        return None, Finding(
            "WARN",
            "conventions",
            repo_root,
            f"commit lookup failed for {sha!r}: {detail}",
        )
    try:
        show = subprocess.run(
            ["git", "-C", str(repo_root), "show", "-s", "--format=%B", sha],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return None, Finding(
            "WARN",
            "conventions",
            repo_root,
            f"commit lookup failed for {sha!r}: {exc}",
        )
    if show.returncode != 0:
        err = (show.stderr or "").strip().splitlines()
        detail = err[0] if err else f"exit {show.returncode}"
        return None, Finding(
            "WARN",
            "conventions",
            repo_root,
            f"commit lookup failed for {sha!r}: {detail}",
        )
    return show.stdout, None


def _read_diff_file(diff_path: Path) -> tuple[str | None, Finding | None]:
    """Read a unified-diff text file. Missing file is a CLI-misuse BLOCK."""
    if not diff_path.is_file():
        return None, Finding(
            "BLOCK",
            "conventions",
            diff_path,
            f"--diff-file path does not exist: {diff_path}",
        )
    try:
        return diff_path.read_text(encoding="utf-8"), None
    except OSError as exc:
        return None, Finding(
            "BLOCK",
            "conventions",
            diff_path,
            f"--diff-file read failed: {exc}",
        )


def _dispatch_conventions(
    args: argparse.Namespace,
    repo_root: Path,
) -> list[Finding]:
    """Run convention shape + pattern checks.

    Without ``--commit`` / ``--diff-file``: shape validation only — the same
    backward-compatible behavior shipped before pattern-firing was wired in.
    With either or both flags, the matching scope is pattern-fired against
    the supplied input so ``commit_body`` and ``diff`` rules actually trip
    the gate instead of being silently skipped.

    Subprocess / IO failures degrade to ``WARN`` findings (commit lookup) or
    ``BLOCK`` findings (missing diff file) and never raise — the validator
    must keep running the rest of the rule set even when an input is
    unavailable.
    """
    commit_body: str | None = None
    diff_text: str | None = None
    extras: list[Finding] = []
    if args.commit is not None:
        commit_body, warn = _read_commit_body(args.commit, repo_root)
        if warn is not None:
            extras.append(warn)
    if args.diff_file is not None:
        diff_text, block = _read_diff_file(args.diff_file)
        if block is not None:
            extras.append(block)
    findings = list(validate_conventions(repo_root, commit_body=commit_body, diff=diff_text))
    return extras + findings


def _dispatch_health(
    args: argparse.Namespace,  # noqa: ARG001
    repo_root: Path,
) -> list[Finding]:
    return list(validate_health(repo_root))


def _dispatch_lessons(
    args: argparse.Namespace,  # noqa: ARG001
    repo_root: Path,
) -> list[Finding]:
    return list(validate_lessons(repo_root))


def _dispatch_ship(
    args: argparse.Namespace,  # noqa: ARG001
    repo_root: Path,
) -> list[Finding]:
    return list(validate_capability_uniqueness(repo_root))


def _validate_feature(
    feature: Path,
    repo_root: Path,
    *,
    check_registries: bool,
) -> list[Finding]:
    """Run every per-feature validator over a single feature directory."""
    findings: list[Finding] = []
    findings.extend(validate_deviations(feature))
    findings.extend(validate_tdd_evidence(repo_root, feature.name))
    findings.extend(validate_domain_glossary(repo_root, feature.name))
    findings.extend(validate_qa_shape(repo_root, feature.name))
    research = feature / "RESEARCH.md"
    if research.is_file():
        findings.extend(validate_research(research))
    spec = feature / SPEC_FILENAME
    plan = feature / PLAN_FILENAME
    if spec.is_file():
        findings.extend(validate_negative_requirements(spec))
        findings.extend(validate_frontmatter(spec, kind="spec"))
        findings.extend(validate_scenarios(spec))
        findings.extend(validate_anchors(spec, repo_root=repo_root))
    if plan.is_file():
        findings.extend(validate_frontmatter(plan, kind="plan"))
        if spec.is_file():
            findings.extend(validate_plan_tasks(plan, spec_path=spec))
        findings.extend(validate_verified_deps(plan, check_registries=check_registries))
    # A feature folder with a state.json is the right scope for the
    # git-conventions structural check. The validator self-gates on the
    # feature-folder path shape and silently passes for features with no
    # ``commits[]``, so the wiring is safe to run unconditionally.
    if (feature / "state.json").is_file():
        findings.extend(validate_git_conventions(feature))
    # Cross-check REVIEW.code.md lesson tags against ``.forge/intel/lessons.md``
    # at validate time so a row Severity / lesson Severity mismatch surfaces in
    # CI instead of waiting until /forge:ship. No-op when either file is absent.
    findings.extend(validate_review_md_lesson_tags(feature, repo_root))
    return findings


def _dispatch_all(args: argparse.Namespace, repo_root: Path) -> list[Finding]:
    """Walk the full .forge tree, invoking every applicable validator.

    Layout signals come from a single ``validate_health`` call. Per-feature
    semantic helpers are invoked directly here (they do NOT re-run
    ``_check_feature_payload``); that avoids double-counting findings
    surfaced by health.
    """
    findings: list[Finding] = []
    findings.extend(validate_health(repo_root))
    findings.extend(validate_capability_uniqueness(repo_root))
    findings.extend(validate_config(repo_root / ".forge" / "config.json"))
    # WS2 shape pass: catch schema / duplicate-id / regex compile / ReDoS-shape /
    # mis-scoped filename_glob_forbidden in ``.forge/conventions.json`` before
    # any feature-level work runs. Pattern-firing checks (commit_body / diff)
    # remain SKILL-driven — the review workflow gathers commit bodies and diffs
    # per feature and calls ``validate_conventions(repo_root, commit_body=...,
    # diff=...)`` directly.
    findings.extend(validate_conventions(repo_root))

    constitution = repo_root / ".forge" / "CONSTITUTION.md"
    if constitution.is_file():
        findings.extend(validate_constitution(constitution))

    # ``.forge/intel/lessons.md`` is optional; the validator short-circuits to
    # an empty list when the file is absent, so calling unconditionally is
    # cheap and keeps the all-target dispatcher symmetric with the per-target
    # ``--target lessons`` path.
    findings.extend(validate_lessons(repo_root))

    changes_root = repo_root / ".forge" / "changes"
    if changes_root.is_dir():
        for change in sorted(changes_root.iterdir()):
            if change.name in _RESERVED_SUBFOLDERS:
                continue
            proposal = change / "proposal.md"
            if proposal.is_file():
                findings.extend(validate_delta(proposal))

    features_root = repo_root / ".forge" / "features"
    if features_root.is_dir():
        for feature in sorted(features_root.iterdir()):
            if not feature.is_dir() or feature.name in _RESERVED_SUBFOLDERS:
                continue
            findings.extend(
                _validate_feature(
                    feature,
                    repo_root,
                    check_registries=args.check_registries,
                )
            )
    return findings


_TARGET_DISPATCH: dict[str, Callable[[argparse.Namespace, Path], list[Finding]]] = {
    "spec": _dispatch_spec,
    "plan": _dispatch_plan,
    "delta": _dispatch_delta,
    "scenarios": _dispatch_scenarios,
    "anchors": _dispatch_anchors,
    "spec-semantic": _dispatch_spec_semantic,
    "plan-tasks": _dispatch_plan_tasks,
    "verified-deps": _dispatch_verified_deps,
    "deviations": _dispatch_deviations,
    "tdd_evidence": _dispatch_tdd_evidence,
    "domain_glossary": _dispatch_domain_glossary,
    "qa_shape": _dispatch_qa_shape,
    "git-conventions": _dispatch_git_conventions,
    "research": _dispatch_research,
    "constitution": _dispatch_constitution,
    "config": _dispatch_config,
    "conventions": _dispatch_conventions,
    "health": _dispatch_health,
    "lessons": _dispatch_lessons,
    "ship": _dispatch_ship,
    "all": _dispatch_all,
}


def _gate_per_file(target: str, args: argparse.Namespace) -> list[Finding]:
    if args.path is None:
        return [
            Finding(
                "BLOCK",
                target,
                Path(),
                f"--target {target} requires a path argument",
            )
        ]
    if not args.path.is_file():
        return [
            Finding(
                "BLOCK",
                target,
                args.path,
                f"--target {target} requires an existing file: {args.path}",
            )
        ]
    return _TARGET_DISPATCH[target](args, args.repo_root)


def _gate_per_folder(target: str, args: argparse.Namespace) -> list[Finding]:
    if args.path is None:
        return [
            Finding(
                "BLOCK",
                target,
                Path(),
                f"--target {target} requires a folder path argument",
            )
        ]
    if not args.path.is_dir():
        return [
            Finding(
                "BLOCK",
                target,
                args.path,
                f"--target {target} requires a directory: {args.path}",
            )
        ]
    return _TARGET_DISPATCH[target](args, args.repo_root)


def _gate_repo_wide(target: str, args: argparse.Namespace) -> list[Finding]:
    findings: list[Finding] = []
    if args.path is not None:
        findings.append(
            Finding(
                "WARN",
                target,
                args.path,
                f"--target {target} ignores positional path argument",
            )
        )
    if not args.repo_root.is_dir():
        findings.append(
            Finding(
                "BLOCK",
                target,
                args.repo_root,
                f"--repo-root {str(args.repo_root)!r} is not a directory; "
                f"point it at the repository root containing the .forge/ tree",
            )
        )
        return findings
    findings.extend(_TARGET_DISPATCH[target](args, args.repo_root))
    return findings


def _gate_and_dispatch(target: str, args: argparse.Namespace) -> list[Finding]:
    """Validate path-kind expectations, then run the target's dispatcher.

    `constitution` is special: it accepts either a positional path or
    falls back to ``<repo-root>/.forge/CONSTITUTION.md``. It is dispatched
    directly without per-file/per-folder gating.
    """
    if target in _PER_FILE_TARGETS:
        return _gate_per_file(target, args)
    if target in _PER_FOLDER_TARGETS:
        return _gate_per_folder(target, args)
    if target in _REPO_WIDE_TARGETS:
        return _gate_repo_wide(target, args)
    # constitution (or any future hybrid target): no path-kind gating.
    return _TARGET_DISPATCH[target](args, args.repo_root)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for /forge:validate. See module-level exit-code contract."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.validate",
        description="FORGE validator (structural + semantic checks)",
    )
    parser.add_argument(
        "--target",
        required=True,
        choices=list(_TARGET_CHOICES),
        help="Which validator to run.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root for repo-wide checks (default: cwd).",
    )
    parser.add_argument(
        "--check-registries",
        action="store_true",
        default=False,
        help=(
            "For verified-deps / all: probe registries (npm, pip) for declared "
            "dependencies. Off by default (offline / shape-only)."
        ),
    )
    parser.add_argument(
        "--commit",
        type=str,
        default=None,
        help=(
            "For --target conventions: SHA to feed as commit_body via "
            "'git show -s --format=%%B'. Other targets accept the flag for "
            "argparse uniformity but ignore it."
        ),
    )
    parser.add_argument(
        "--diff-file",
        type=Path,
        default=None,
        help=(
            "For --target conventions: path to a unified-diff text file to "
            "feed as the diff scope. Other targets accept the flag for "
            "argparse uniformity but ignore it."
        ),
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        help=(
            "Per-file targets: path to artifact (SPEC.md/PLAN.md/proposal.md). "
            "Per-folder targets (deviations): the feature folder. "
            "Repo-wide targets ignore this argument with a WARN."
        ),
    )
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2

    target: str = args.target
    findings: list[Finding] = _gate_and_dispatch(target, args)

    payload = {
        "target": target,
        "findings": [_finding_to_dict(f) for f in findings],
    }
    print(json.dumps(payload, indent=2))

    counts: dict[str, int] = {}
    for f in findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    summary_parts = [f"validate: target={target}", f"findings={len(findings)}"]
    summary_parts.extend(
        f"{sev.lower()}={counts[sev]}"
        for sev in ("BLOCK", "HIGH", "MEDIUM", "LOW", "WARN", "INFO")
        if counts.get(sev)
    )
    print(" ".join(summary_parts), file=sys.stderr)

    has_exit_severity = any(f.severity in EXIT_NONZERO_SEVERITIES for f in findings)
    return 1 if has_exit_severity else 0

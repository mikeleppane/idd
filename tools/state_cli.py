"""Bash CLI entry point for ``tools.state.*`` post-seed mutators.

Wraps the six post-seed mutators in an argparse surface so the
state-touching skills (forge-refine, forge-spec, forge-scenarios,
forge-plan, forge-crucible, forge-execute, forge-verify, forge-ship)
can collapse to a single mechanical Bash invocation instead of a Python
heredoc that agents consistently improvise the call shape on (positional
vs keyword-only, missing keyword args, wrong type for slice_number, ...).

The state-writer hook (``hooks/check_state_writer.py``) refuses direct
Write/Edit/MultiEdit against ``state.json``. The hook deny message
points at ``forge-do`` for the initial seed and at this CLI
(``forge-state``) for every subsequent transition, closing the
improvisation surface end-to-end.

Subcommands mirror :mod:`tools.state` one-to-one:

    forge-state refine --feature ID --refined TEXT
    forge-state complete-phase --feature ID --phase NAME
    forge-state start-phase --feature ID --phase NAME [--force]
    forge-state set-current-slice --feature ID --slice N
    forge-state record-commit --feature ID --sha SHA --phase NAME --subject TEXT
    forge-state deviation --feature ID --phase NAME --cause TEXT --resolution TEXT
    forge-state complete-review-target --feature ID --target {plan,code}
    forge-state finish --feature ID

All subcommands resolve ``--feature ID`` to
``<repo_root>/.forge/features/<ID>/state.json`` where ``<repo_root>``
defaults to the current working directory and can be overridden with
``--repo-root``.

Exit codes:

  * 0 — mutation succeeded.
  * 1 — helper-level refusal (StateError / PhasePreconditionError). The
    underlying message lands on stderr.
  * 2 — argparse usage error (argparse's default).

Unexpected exceptions propagate with their traceback intact — those are
real bugs the operator should see, not user errors to swallow.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from tools.state import (
    VALID_LIFECYCLE_PHASES,
    VALID_REVIEW_TARGETS,
    StateError,
    append_deviation,
    complete_phase,
    complete_review_target,
    finish_feature,
    record_commit,
    record_refined_idea,
    set_execute_current_slice,
    start_phase,
)


def _state_path(repo_root: Path, feature_id: str) -> Path:
    """Resolve <repo_root>/.forge/features/<feature_id>/state.json."""
    return repo_root / ".forge" / "features" / feature_id / "state.json"


def _build_parser() -> argparse.ArgumentParser:
    """Build the ``forge-state`` argparse surface with subcommands."""
    parser = argparse.ArgumentParser(
        prog="forge-state",
        description=(
            "Mutate a FORGE feature's state.json via the canonical "
            "tools.state.* helpers. One Bash subcommand per mutator; "
            "kills the Python-heredoc improvisation surface."
        ),
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root containing .forge/. Defaults to the current working directory.",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # refine
    p_refine = sub.add_parser(
        "refine",
        help="Persist refined idea paragraph (record_refined_idea).",
    )
    p_refine.add_argument("--feature", required=True, help="Feature ID under .forge/features/.")
    p_refine.add_argument("--refined", required=True, help="Single-paragraph refined idea text.")

    # complete-phase
    p_complete = sub.add_parser(
        "complete-phase",
        help="Mark the current phase as done (complete_phase).",
    )
    p_complete.add_argument("--feature", required=True)
    p_complete.add_argument(
        "--phase",
        required=True,
        choices=VALID_LIFECYCLE_PHASES,
        help="Lifecycle phase to complete (must equal current_phase).",
    )

    # start-phase
    p_start = sub.add_parser(
        "start-phase",
        help="Advance current_phase to the next slot (start_phase).",
    )
    p_start.add_argument("--feature", required=True)
    p_start.add_argument("--phase", required=True, choices=VALID_LIFECYCLE_PHASES)
    p_start.add_argument(
        "--force",
        action="store_true",
        help=(
            "Bypass precondition check. Reserved for short-lived recovery "
            "scripts; prefer tools.recovery.recover_force_start_phase for "
            "audited recoveries."
        ),
    )

    # set-current-slice
    p_slice = sub.add_parser(
        "set-current-slice",
        help="Stamp execute.current_slice cursor (set_execute_current_slice).",
    )
    p_slice.add_argument("--feature", required=True)
    p_slice.add_argument("--slice", required=True, type=int, dest="slice_number")

    # record-commit
    p_commit = sub.add_parser(
        "record-commit",
        help="Append an entry to state.commits[] (record_commit).",
    )
    p_commit.add_argument("--feature", required=True)
    p_commit.add_argument("--sha", required=True, help="7-40 lowercase hex git SHA.")
    p_commit.add_argument("--phase", required=True, choices=VALID_LIFECYCLE_PHASES)
    p_commit.add_argument("--subject", required=True, help="Commit subject line.")

    # deviation
    p_dev = sub.add_parser(
        "deviation",
        help="Append an entry to state.deviations[] (append_deviation).",
    )
    p_dev.add_argument("--feature", required=True)
    p_dev.add_argument("--phase", required=True, choices=VALID_LIFECYCLE_PHASES)
    p_dev.add_argument("--cause", required=True)
    p_dev.add_argument("--resolution", required=True)

    # complete-review-target
    p_rt = sub.add_parser(
        "complete-review-target",
        help="Mark a review target done (complete_review_target).",
    )
    p_rt.add_argument("--feature", required=True)
    p_rt.add_argument(
        "--target",
        required=True,
        choices=VALID_REVIEW_TARGETS,
        help="Review target to record as done (must equal phases.review.current_target).",
    )

    # finish
    p_finish = sub.add_parser(
        "finish",
        help="Set current_phase='done' on focused-tier completion (finish_feature).",
    )
    p_finish.add_argument("--feature", required=True)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``forge-state`` CLI end to end.

    Args:
        argv: Optional argv override (argparse reads from ``sys.argv[1:]``
            when omitted).

    Returns:
        Exit code: 0 on success, 1 on helper refusal.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    repo_root: Path = args.repo_root if args.repo_root is not None else Path.cwd()
    path = _state_path(repo_root, args.feature)

    try:
        if args.command == "refine":
            record_refined_idea(path, refined=args.refined)
        elif args.command == "complete-phase":
            complete_phase(path, args.phase)
        elif args.command == "start-phase":
            start_phase(path, args.phase, force=args.force)
        elif args.command == "set-current-slice":
            set_execute_current_slice(path, slice_number=args.slice_number)
        elif args.command == "record-commit":
            record_commit(path, sha=args.sha, phase=args.phase, subject=args.subject)
        elif args.command == "deviation":
            append_deviation(
                path,
                phase=args.phase,
                cause=args.cause,
                resolution=args.resolution,
            )
        elif args.command == "complete-review-target":
            complete_review_target(path, review_target=args.target)
        elif args.command == "finish":
            finish_feature(path)
        else:  # pragma: no cover — argparse enforces subcommand membership
            parser.error(f"unknown command {args.command!r}")
    except StateError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"ok: {args.command} feature={args.feature}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

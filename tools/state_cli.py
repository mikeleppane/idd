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
import json
import os
import re
import sys
import tempfile
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import yaml

from tools.migrations.registry import MigrationRegistryError, apply_pending
from tools.state import (
    VALID_LIFECYCLE_PHASES,
    VALID_REVIEW_TARGETS,
    StateError,
    _atomic_write_json,
    append_deviation,
    complete_phase,
    complete_review_target,
    find_active_feature,
    finish_feature,
    record_commit,
    record_refined_idea,
    set_execute_current_slice,
    start_phase,
)

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)

_JSON_FILE_KINDS: dict[str, str] = {
    "conventions.json": "conventions",
}

# Repo-level .forge/config.json is structured as { <block>: { ... } }; the
# block names map to the registered file kinds whose schemas validate each
# subblock. The standalone files cross-ai-config.json etc. do not exist in a
# real repo, only the subblocks; map them here instead of in _JSON_FILE_KINDS.
_CONFIG_SUBBLOCK_KIND: dict[str, str] = {
    "git_conventions": "git-conventions-config",
    "cross_ai": "cross-ai-config",
    "research": "research-config",
}

_MARKDOWN_FILE_KINDS: dict[str, str] = {
    "SPEC.md": "spec",
    "PLAN.md": "plan",
    "RESEARCH.md": "research",
    "UNDERSTANDING.md": "understanding",
    "REVIEW.md": "review",
    "CONSTITUTION.md": "constitution",
    "proposal.md": "delta-proposal",
}


def _state_path(repo_root: Path, feature_id: str) -> Path:
    """Resolve <repo_root>/.forge/features/<feature_id>/state.json."""
    return repo_root / ".forge" / "features" / feature_id / "state.json"


def _file_kind(path: Path) -> str | None:
    if path.suffix == ".json":
        return _JSON_FILE_KINDS.get(path.name)
    if path.suffix == ".md":
        if path.name.startswith("REVIEW.") and path.name.endswith(".md"):
            return "review"
        return _MARKDOWN_FILE_KINDS.get(path.name)
    return None


def _read_json_doc(path: Path) -> dict[str, Any]:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise StateError(f"{path}: JSON document must be an object")
    return cast(dict[str, Any], parsed)


def _read_markdown_doc(path: Path) -> tuple[dict[str, Any], str, str]:
    """Return (frontmatter, body, line_ending) for a markdown file with frontmatter.

    Reads bytes and decodes manually instead of ``path.read_text`` so universal
    newline translation cannot collapse CRLF down to LF before the rewrite path
    sees it; otherwise migrating a CRLF file would silently convert it to LF
    on every run.
    """
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StateError(f"{path}: not valid UTF-8: {exc}") from exc
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        raise StateError(f"{path}: missing YAML frontmatter")
    try:
        parsed = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        raise StateError(f"{path}: invalid YAML frontmatter: {exc}") from exc
    if not isinstance(parsed, dict):
        raise StateError(f"{path}: YAML frontmatter must be a mapping")
    line_ending = "\r\n" if "\r\n" in text[: match.end()] else "\n"
    return cast(dict[str, Any], parsed), text[match.end() :], line_ending


def _atomic_write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` atomically via tempfile + ``os.replace``.

    Mirrors :func:`tools.state._atomic_write_json` so the markdown rewrite path
    cannot leave a half-written SPEC.md/PLAN.md after a SIGKILL or power loss.
    The tempfile is created in the same directory so ``os.replace`` is a
    same-filesystem rename, which the kernel guarantees is atomic on
    POSIX-compliant filesystems.
    """
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        tmp_path.replace(path)
    except BaseException:
        with suppress(OSError):
            tmp_path.unlink()
        raise


def _write_markdown_doc(
    path: Path,
    frontmatter: dict[str, Any],
    body: str,
    line_ending: str = "\n",
) -> None:
    yaml_text = yaml.safe_dump(
        frontmatter,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
    )
    if line_ending == "\r\n":
        # yaml.safe_dump always emits LF; convert the rebuilt frontmatter
        # block to CRLF so a CRLF source file doesn't get silently normalised
        # to LF on every migration. The body slice already carries CRLF
        # because we kept it byte-for-byte from the original file.
        yaml_text = yaml_text.replace("\n", "\r\n")
    content = f"---{line_ending}{yaml_text}---{line_ending}{body}"
    _atomic_write_text(path, content)


def _migrate_doc(path: Path, file_kind: str, doc: dict[str, Any]) -> dict[str, Any]:
    try:
        migrated = apply_pending(file_kind, doc)
    except MigrationRegistryError as exc:
        raise StateError(f"{path}: {exc}") from exc
    if "schema_version" not in doc and "schema_version" not in migrated:
        migrated["schema_version"] = 1
    return migrated


def _migration_message(
    path: Path,
    feature_folder: Path,
    file_kind: str,
    original: dict[str, Any],
    migrated: dict[str, Any],
) -> str:
    rel = path.relative_to(feature_folder)
    before = original.get("schema_version", 1)
    after = migrated.get("schema_version", before)
    label = "implicit 1" if "schema_version" not in original else str(before)
    message = f"{rel}: {file_kind} schema_version {label} -> {after}"
    if "schema_version" not in original and after == 1:
        message += "; add schema_version: 1"
    return message


def _migrate_feature(feature_folder: Path, *, dry_run: bool) -> int:
    changed = 0
    for path in sorted(p for p in feature_folder.rglob("*") if p.is_file()):
        # Refuse to follow symlinks: rglob preserves the symlinked path, so a
        # symlink inside the feature folder pointing outside it would otherwise
        # be read and (worse) rewritten through the dereferenced target.
        if path.is_symlink():
            print(
                f"skip: {path.relative_to(feature_folder)}: symlink (refusing to follow)",
                file=sys.stderr,
            )
            continue
        if path.suffix not in {".json", ".md"}:
            continue
        file_kind = _file_kind(path)
        if file_kind is None:
            print(
                f"skip: {path.relative_to(feature_folder)}: unknown file kind",
                file=sys.stderr,
            )
            continue

        if path.suffix == ".json":
            original = _read_json_doc(path)
            migrated = _migrate_doc(path, file_kind, original)
            if migrated == original:
                continue
            changed += 1
            message = _migration_message(path, feature_folder, file_kind, original, migrated)
            if dry_run:
                print(f"dry-run: would migrate {message}")
            else:
                _atomic_write_json(path, migrated)
                print(f"migrated: {message}")
            continue

        original, body, line_ending = _read_markdown_doc(path)
        migrated = _migrate_doc(path, file_kind, original)
        if migrated == original:
            continue
        changed += 1
        message = _migration_message(path, feature_folder, file_kind, original, migrated)
        if dry_run:
            print(f"dry-run: would migrate {message}")
        else:
            _write_markdown_doc(path, migrated, body, line_ending=line_ending)
            print(f"migrated: {message}")
    return changed


def _migrate_repo_config(repo_root: Path, *, dry_run: bool) -> int:
    """Apply pending migrations to each subblock of ``.forge/config.json``.

    Returns the number of files written. The config file is optional; absence
    is silent so a freshly-initialized repo does not warn.
    """
    config_path = repo_root / ".forge" / "config.json"
    if not config_path.is_file():
        return 0
    original_payload = _read_json_doc(config_path)

    new_payload = dict(original_payload)
    changed_blocks: list[str] = []
    for block_name, kind in _CONFIG_SUBBLOCK_KIND.items():
        block = new_payload.get(block_name)
        if not isinstance(block, dict):
            continue
        migrated_block = _migrate_doc(config_path, kind, block)
        if migrated_block != block:
            new_payload[block_name] = migrated_block
            changed_blocks.append(block_name)

    if not changed_blocks:
        return 0
    label = ", ".join(changed_blocks)
    if dry_run:
        print(f"dry-run: would migrate {config_path}: subblocks={label}")
    else:
        _atomic_write_json(config_path, new_payload)
        print(f"migrated: {config_path}: subblocks={label}")
    return 1


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

    # migrate
    p_migrate = sub.add_parser(
        "migrate",
        help="Run pending schema migrations for a feature folder.",
    )
    p_migrate.add_argument(
        "--feature",
        help=(
            "Feature ID under .forge/features/. Omit to target the single "
            "active feature; the helper refuses ambiguously when multiple are "
            "active. Skipped entirely when --repo-only is set."
        ),
    )
    p_migrate.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned migrations without modifying files.",
    )
    p_migrate.add_argument(
        "--include-repo-config",
        action="store_true",
        help=(
            "Also migrate repo-level .forge/config.json subblocks "
            "(git_conventions, cross_ai, research)."
        ),
    )
    p_migrate.add_argument(
        "--repo-only",
        action="store_true",
        help=(
            "Skip feature-folder migration; only touch repo-level "
            ".forge/config.json. Implies --include-repo-config."
        ),
    )

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

    try:
        if args.command == "refine":
            path = _state_path(repo_root, args.feature)
            record_refined_idea(path, refined=args.refined)
        elif args.command == "complete-phase":
            path = _state_path(repo_root, args.feature)
            complete_phase(path, args.phase)
        elif args.command == "start-phase":
            path = _state_path(repo_root, args.feature)
            start_phase(path, args.phase, force=args.force)
        elif args.command == "set-current-slice":
            path = _state_path(repo_root, args.feature)
            set_execute_current_slice(path, slice_number=args.slice_number)
        elif args.command == "record-commit":
            path = _state_path(repo_root, args.feature)
            record_commit(path, sha=args.sha, phase=args.phase, subject=args.subject)
        elif args.command == "deviation":
            path = _state_path(repo_root, args.feature)
            append_deviation(
                path,
                phase=args.phase,
                cause=args.cause,
                resolution=args.resolution,
            )
        elif args.command == "complete-review-target":
            path = _state_path(repo_root, args.feature)
            complete_review_target(path, review_target=args.target)
        elif args.command == "finish":
            path = _state_path(repo_root, args.feature)
            finish_feature(path)
        elif args.command == "migrate":
            include_repo = args.include_repo_config or args.repo_only
            dry_run_suffix = " dry_run=true" if args.dry_run else ""

            if args.repo_only:
                repo_changed = _migrate_repo_config(repo_root, dry_run=args.dry_run)
                print(f"ok: migrate scope=repo changed={repo_changed}{dry_run_suffix}")
                return 0

            feature_folder = find_active_feature(repo_root, feature_id=args.feature)
            changed = _migrate_feature(feature_folder, dry_run=args.dry_run)
            repo_changed = (
                _migrate_repo_config(repo_root, dry_run=args.dry_run) if include_repo else 0
            )
            print(
                f"ok: migrate feature={feature_folder.name} changed={changed} "
                f"repo_changed={repo_changed}{dry_run_suffix}"
                if include_repo
                else f"ok: migrate feature={feature_folder.name} changed={changed}{dry_run_suffix}"
            )
            return 0
        else:  # pragma: no cover — argparse enforces subcommand membership
            parser.error(f"unknown command {args.command!r}")
    except StateError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"ok: {args.command} feature={args.feature}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

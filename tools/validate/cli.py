"""CLI entry point for /idd:validate (M3 §5.3.6 D-CLI)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from ._finding import EXIT_NONZERO_SEVERITIES, Finding, _finding_to_dict
from .constitution import validate_constitution
from .delta import validate_delta
from .health import validate_health
from .spec_structural import (
    validate_capability_uniqueness,
    validate_frontmatter,
    validate_negative_requirements,
)

_PER_FILE_TARGETS: frozenset[str] = frozenset({"spec", "plan", "delta"})
_REPO_WIDE_TARGETS: frozenset[str] = frozenset({"health", "ship", "all"})


def _dispatch_target(target: str, path: Path | None, repo_root: Path) -> list[Finding]:
    """Run the validator(s) for *target* and return all findings."""
    findings: list[Finding] = []

    if target in _PER_FILE_TARGETS and path is None:
        findings.append(
            Finding(
                "BLOCK",
                target,
                Path(),
                f"--target {target} requires a path argument",
            )
        )
    elif target == "delta" and path is not None:
        findings.extend(validate_delta(path))
    elif target == "spec" and path is not None:
        findings.extend(validate_negative_requirements(path))
        findings.extend(validate_frontmatter(path, kind="spec"))
    elif target == "plan" and path is not None:
        findings.extend(validate_frontmatter(path, kind="plan"))
    elif target == "constitution":
        resolved = path or repo_root / ".idd" / "CONSTITUTION.md"
        findings.extend(validate_constitution(resolved))
    elif target == "ship":
        findings.extend(validate_capability_uniqueness(repo_root))
    elif target in {"health", "all"}:
        # P2a deviation: `all` is staged to `health` only. Per-file fan-out
        # over .idd/specs/, .idd/changes/, .idd/features/ ships in P2b
        # alongside the semantic checks. See commands/validate.md.
        findings.extend(validate_health(repo_root))

    return findings


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for /idd:validate. See module-level exit-code contract."""
    parser = argparse.ArgumentParser(
        prog="python -m tools.validate",
        description="IDD structural validator (M3 P2a)",
    )
    parser.add_argument(
        "--target",
        required=True,
        choices=[
            "spec",
            "plan",
            "delta",
            "constitution",
            "ship",
            "health",
            "all",
        ],
        help="Which validator to run.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root for repo-wide checks (default: cwd).",
    )
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        help="Optional path to a single artifact for per-file targets.",
    )
    try:
        args = parser.parse_args(list(argv) if argv is not None else None)
    except SystemExit as exc:
        # argparse already wrote the usage error to stderr.
        return exc.code if isinstance(exc.code, int) else 2

    target = args.target
    findings: list[Finding] = []

    if target in _REPO_WIDE_TARGETS and args.path is not None:
        findings.append(
            Finding(
                "WARN",
                target,
                args.path,
                f"--target {target} ignores positional path argument",
            ),
        )

    if target in _REPO_WIDE_TARGETS and not args.repo_root.is_dir():
        findings.append(
            Finding(
                "BLOCK",
                target,
                args.repo_root,
                f"--repo-root {str(args.repo_root)!r} is not a directory; "
                f"point it at the repository root containing the .idd/ tree",
            ),
        )
    else:
        findings.extend(_dispatch_target(target, args.path, args.repo_root))

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

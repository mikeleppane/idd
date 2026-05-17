"""Enforce a per-file coverage floor against a Cobertura ``coverage.xml``.

The gate ratchets each baseline-listed file to ``max(absolute_floor, baseline_pct)``
and refuses to pass when any file's measured line-rate falls below that floor.
Files absent from the baseline are ignored; files listed in the baseline but
missing from ``coverage.xml`` are a configuration error.

CLI:

    python -m tools.check_coverage_floor <coverage.xml>
        [--baseline tests/_baselines/coverage.txt]
        [--absolute-floor 85]

Exit codes:

* 0 — every baseline-listed file is at or above its floor.
* 1 — one or more breaches; ``BREACH`` lines printed to stdout.
* 2 — configuration error (missing files, malformed baseline, etc.).
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

DEFAULT_BASELINE = "tests/_baselines/coverage.txt"
DEFAULT_ABSOLUTE_FLOOR = 85

_BASELINE_LINE_RE = re.compile(r"^([^:#]+):\s*(\d+(?:\.\d+)?)%\s*$")


def _parse_baseline(path: Path) -> dict[str, float]:
    """Parse a baseline file into ``{filename: percent}``.

    Raises ``ValueError`` for malformed non-blank, non-comment lines.
    """
    entries: dict[str, float] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _BASELINE_LINE_RE.match(stripped)
        if match is None:
            msg = f"malformed baseline line {lineno}: {raw!r}"
            raise ValueError(msg)
        filename, pct_text = match.group(1).strip(), match.group(2)
        entries[filename] = float(pct_text)
    return entries


def _parse_coverage_xml(path: Path) -> dict[str, float]:
    """Parse a Cobertura XML and return ``{filename: percent}`` (line-rate times 100)."""
    tree = ET.parse(path)  # noqa: S314 - trusted CI input
    root = tree.getroot()
    out: dict[str, float] = {}
    for cls in root.iter("class"):
        filename = cls.get("filename")
        rate_text = cls.get("line-rate")
        if filename is None or rate_text is None:
            continue
        try:
            rate = float(rate_text)
        except ValueError:
            continue
        out[filename] = rate * 100.0
    return out


def _load_inputs(
    coverage_xml: Path, baseline_path: Path
) -> tuple[dict[str, float], dict[str, float]] | int:
    """Validate paths and parse both inputs.

    Returns ``(baseline, measured)`` on success, or an integer exit code on
    configuration error (after printing a message to stderr).
    """
    if not baseline_path.is_file():
        print(f"error: baseline file not found: {baseline_path}", file=sys.stderr)
        return 2
    if not coverage_xml.is_file():
        print(f"error: coverage.xml not found: {coverage_xml}", file=sys.stderr)
        return 2

    try:
        baseline = _parse_baseline(baseline_path)
    except ValueError as exc:
        print(f"error: malformed baseline file: {exc}", file=sys.stderr)
        return 2

    try:
        measured = _parse_coverage_xml(coverage_xml)
    except ET.ParseError as exc:
        print(f"error: failed to parse coverage.xml: {exc}", file=sys.stderr)
        return 2

    missing = [path for path in baseline if path not in measured]
    if missing:
        joined = ", ".join(sorted(missing))
        print(
            f"error: baseline-listed file(s) absent from coverage.xml: {joined}",
            file=sys.stderr,
        )
        return 2

    return baseline, measured


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    parser = argparse.ArgumentParser(
        prog="check_coverage_floor",
        description="Enforce per-file coverage floor against a Cobertura coverage.xml.",
    )
    parser.add_argument("coverage_xml", type=Path, help="path to Cobertura coverage.xml")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path(DEFAULT_BASELINE),
        help=f"path to baseline file (default: {DEFAULT_BASELINE})",
    )
    parser.add_argument(
        "--absolute-floor",
        type=int,
        default=DEFAULT_ABSOLUTE_FLOOR,
        help=f"absolute coverage floor as integer percent (default: {DEFAULT_ABSOLUTE_FLOOR})",
    )
    args = parser.parse_args(argv)

    loaded = _load_inputs(args.coverage_xml, args.baseline)
    if isinstance(loaded, int):
        return loaded
    baseline, measured = loaded
    absolute_floor: int = args.absolute_floor

    breaches: list[str] = []
    for path, baseline_pct in sorted(baseline.items()):
        floor = max(float(absolute_floor), baseline_pct)
        actual = measured[path]
        if actual < floor:
            breaches.append(f"BREACH {path}: actual {actual:.1f}% < floor {floor:.0f}%")

    files_checked = len(baseline)
    if breaches:
        for line in breaches:
            print(line)
        print(f"Gate FAIL ({len(breaches)} breaches, {files_checked} files checked)")
        return 1

    print(f"Gate PASS ({files_checked} files checked)")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    sys.exit(main())

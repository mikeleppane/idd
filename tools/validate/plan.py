r"""Semantic validators for PLAN.md (M3 §5.3.6 D-8, P2b).

Two validators live here:

``validate_plan_tasks`` — migrates ``skills/idd-plan/SKILL.md:35-37`` self-review
rules:

- Every numbered AC index in the paired SPEC's ``# Acceptance Criteria`` body
  slice is unblocked by **exactly one** slice (HIGH on count != 1).
- No file appears in more than one slice's ``**Files in scope:**`` unless the
  entry is flagged ``shared:`` (HIGH).
- Every slice declares an ``**Acceptance:**`` line (HIGH if missing).

``validate_verified_deps`` — migrates the M2 plan-skill placeholder
("Validator (M3+) will check registry presence" — ``skills/idd-plan/SKILL.md:33``).
Validates ``## Verified Dependencies`` table shape per master design §7.3.
Live registry probe is opt-in via ``check_registries=True``; offline by default
to keep CI deterministic.

Section parsing strategy mirrors :mod:`spec_semantic`:

- AC indices are extracted from the body slice between
  ``^# Acceptance Criteria\\b`` and the next ``^# `` heading. Numbered lists in
  ``# Open Questions`` etc. are NOT ACs.
- AC reference tokens are explicit: ``crit-N``, ``criterion-N``,
  ``criterion: N``, ``Scenario N`` (case-insensitive). Bare digits do not
  match.
- ``**Files in scope:**`` entries are split on commas, stripped, and have
  surrounding backticks / quote characters removed before the collision
  check.

Slice cap (``<= 4 in standard tier``) is intentionally NOT enforced here: it
requires reading ``state.json``'s ``tier`` field, which a stand-alone PLAN
validator cannot guarantee. Deferred to a tier-aware validator in P3.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

from ._finding import Finding
from ._frontmatter import _read_text

_SLICE_HEADING = re.compile(r"^# Slice (\d+)[:\s].*$", re.MULTILINE)
_FILES_IN_SCOPE = re.compile(r"^\*\*Files in scope:\*\*\s*(.+)$", re.MULTILINE)
_ACCEPTANCE_LINE = re.compile(r"^\*\*Acceptance:\*\*\s*(.+)$", re.MULTILINE)
_AC_TOKEN = re.compile(
    r"\b(?:crit-|criterion-|criterion:\s*|Scenario\s+)(\d+)\b",
    re.IGNORECASE,
)
_ACCEPTANCE_BLOCK = re.compile(r"(?ms)^# Acceptance Criteria\b[^\n]*\n(?P<body>.*?)(?=^# |\Z)")
_NUMBERED_AC = re.compile(r"^(\d+)\.\s+(.+)$", re.MULTILINE)


def _parse_plan_slices(plan_body: str) -> list[tuple[int, str]]:
    """Return ordered (slice_index, slice_body) tuples for every ``# Slice N`` heading."""
    matches = list(_SLICE_HEADING.finditer(plan_body))
    slices: list[tuple[int, str]] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(plan_body)
        slices.append((int(match.group(1)), plan_body[start:end]))
    return slices


_MIN_QUOTED_LEN = 2


def _clean_scope_entry(raw: str) -> str:
    """Strip whitespace, surrounding backticks, and quote characters."""
    entry = raw.strip()
    for quote in ("`", '"', "'"):
        if len(entry) >= _MIN_QUOTED_LEN and entry.startswith(quote) and entry.endswith(quote):
            entry = entry[1:-1].strip()
    return entry


def _spec_ac_indices(spec_text: str) -> list[int]:
    """Extract AC indices from the ``# Acceptance Criteria`` body slice only."""
    block = _ACCEPTANCE_BLOCK.search(spec_text)
    if block is None:
        return []
    return [int(match.group(1)) for match in _NUMBERED_AC.finditer(block.group("body"))]


def validate_plan_tasks(plan_path: Path, *, spec_path: Path) -> list[Finding]:
    """Validate PLAN.md slice mapping against the paired SPEC's Acceptance Criteria.

    Args:
        plan_path: Path to the PLAN.md file under audit.
        spec_path: Path to the paired SPEC.md file. AC indices are sourced
            from this file's ``# Acceptance Criteria`` section.

    Returns:
        List of Finding records. Empty list means the plan satisfies all
        three rules above. Missing / unreadable PLAN or SPEC produces a
        single BLOCK finding.
    """
    plan_text = _read_text(plan_path)
    if plan_text is None:
        return [
            Finding(
                "BLOCK",
                "plan-tasks",
                plan_path,
                f"missing or unreadable: {plan_path}",
            )
        ]
    spec_text = _read_text(spec_path)
    if spec_text is None:
        return [
            Finding(
                "BLOCK",
                "plan-tasks",
                spec_path,
                f"paired SPEC missing or unreadable: {spec_path}",
            )
        ]

    # PLAN body is parsed verbatim: `_strip_code` would replace backticked
    # `Files in scope:` entries with whitespace and defeat the backtick-aware
    # collision check (`_clean_scope_entry`). The plan template has no
    # legitimate fenced/inline code regions to mask anyway.
    # SPEC is also NOT stripped: `_ACCEPTANCE_BLOCK` already isolates the AC
    # section, and the AC numbered list lives outside any code fence.
    ac_indices = _spec_ac_indices(spec_text)
    slices = _parse_plan_slices(plan_text)

    findings: list[Finding] = []
    ac_unblocked: dict[int, list[int]] = defaultdict(list)
    file_owner: dict[str, list[int]] = defaultdict(list)

    for slice_idx, slice_body in slices:
        findings.extend(_scan_slice(plan_path, slice_idx, slice_body, ac_unblocked, file_owner))

    findings.extend(_check_ac_coverage(plan_path, ac_indices, ac_unblocked))
    findings.extend(_check_file_collisions(plan_path, file_owner))
    return findings


def _scan_slice(
    plan_path: Path,
    slice_idx: int,
    slice_body: str,
    ac_unblocked: dict[int, list[int]],
    file_owner: dict[str, list[int]],
) -> list[Finding]:
    """Record AC tokens + file ownership for one slice; emit missing-Acceptance HIGH."""
    findings: list[Finding] = []
    accept = _ACCEPTANCE_LINE.search(slice_body)
    if accept is None:
        findings.append(
            Finding(
                "HIGH",
                "plan-tasks",
                plan_path,
                f"slice {slice_idx} missing `**Acceptance:**` line",
            )
        )
    else:
        # Dedupe AC tokens within a single slice. The shipped PLAN template
        # writes `**Acceptance:** <Scenario 1 passes + criterion-1 met>` which
        # matches AC 1 twice via two alternations of `_AC_TOKEN`. Without this
        # set, `ac_unblocked[1]` would carry [slice_idx, slice_idx] and the
        # multi-slice check below would fire a false HIGH.
        seen_in_slice: set[int] = set()
        for tok in _AC_TOKEN.finditer(accept.group(1)):
            idx = int(tok.group(1))
            if idx in seen_in_slice:
                continue
            seen_in_slice.add(idx)
            ac_unblocked[idx].append(slice_idx)

    files = _FILES_IN_SCOPE.search(slice_body)
    if files:
        for raw in files.group(1).split(","):
            entry = _clean_scope_entry(raw)
            if not entry or entry.startswith("shared:"):
                continue
            file_owner[entry].append(slice_idx)
    return findings


def _check_ac_coverage(
    plan_path: Path,
    ac_indices: list[int],
    ac_unblocked: dict[int, list[int]],
) -> list[Finding]:
    """Emit HIGH findings for ACs unblocked by zero or by more than one slice."""
    findings: list[Finding] = []
    for ac in ac_indices:
        slices_for_ac = ac_unblocked.get(ac, [])
        if len(slices_for_ac) == 0:
            findings.append(
                Finding(
                    "HIGH",
                    "plan-tasks",
                    plan_path,
                    f"AC {ac} unblocked by zero slices",
                )
            )
        elif len(slices_for_ac) > 1:
            findings.append(
                Finding(
                    "HIGH",
                    "plan-tasks",
                    plan_path,
                    f"AC {ac} unblocked by multiple slices: {slices_for_ac}",
                )
            )
    return findings


def _check_file_collisions(
    plan_path: Path,
    file_owner: dict[str, list[int]],
) -> list[Finding]:
    """Emit HIGH findings for files claimed by more than one slice."""
    findings: list[Finding] = []
    for fpath, owners in file_owner.items():
        if len(owners) > 1:
            findings.append(
                Finding(
                    "HIGH",
                    "plan-tasks",
                    plan_path,
                    f"file {fpath!r} appears in multiple slices: {owners}",
                )
            )
    return findings


_VERIFIED_DEPS_BLOCK = re.compile(
    r"(?ms)^## Verified Dependencies\b[^\n]*\n(?P<body>.*?)(?=^## |\Z)"
)
_TABLE_ROW = re.compile(r"^\|(?P<row>.*)\|\s*$", re.MULTILINE)
_REQUIRED_COLUMNS: tuple[str, ...] = ("package", "version range", "registry")
_KNOWN_ECOSYSTEMS: frozenset[str] = frozenset(
    {"npm", "pypi", "crates", "go", "maven", "nuget", "gem"}
)
_SEPARATOR_ROW = re.compile(r"^[\s|:\-]+$")
_REGISTRY_PROBE_TIMEOUT_SECONDS = 5


def _split_table_row(raw: str) -> list[str]:
    """Split a markdown table row into cells preserving index alignment.

    Drops only the leading and trailing empty cells produced by the bracketing
    pipes (e.g. ``|a|b|`` → ``["", "a", "b", ""]`` → ``["a", "b"]``). Interior
    empty cells are KEPT so column indexes stay aligned with the header.
    """
    parts = raw.split("|")
    if parts and parts[0] == "":
        parts = parts[1:]
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    return [c.strip() for c in parts]


def _normalize_header_cell(cell: str) -> str:
    """Lowercase + collapse the master-design ``Version / range`` form."""
    lowered = cell.strip().lower()
    if lowered == "version / range":
        return "version range"
    return lowered


def validate_verified_deps(plan_path: Path, *, check_registries: bool = False) -> list[Finding]:
    """Validate ``## Verified Dependencies`` table shape in PLAN.md.

    Args:
        plan_path: Path to the PLAN.md file under audit.
        check_registries: When True, shell out to the registry CLI for each
            row (npm/pypi only; other ecosystems WARN). Defaults to False so
            offline CI runs stay deterministic.

    Returns:
        List of Finding records. Empty list means the section is absent
        (no deps declared) or the table is well-formed and (when probed)
        every package was found.
    """
    plan_text = _read_text(plan_path)
    if plan_text is None:
        return [
            Finding(
                "BLOCK",
                "verified-deps",
                plan_path,
                f"missing or unreadable: {plan_path}",
            )
        ]

    # Do NOT _strip_code here — the table is markdown, not fenced.
    block = _VERIFIED_DEPS_BLOCK.search(plan_text)
    if block is None:
        return []  # No deps declared = no table required.

    raw_rows = [m.group("row") for m in _TABLE_ROW.finditer(block.group("body"))]
    if not raw_rows:
        return [
            Finding(
                "HIGH",
                "verified-deps",
                plan_path,
                "Verified Dependencies section has no table",
            )
        ]

    header_cells = [_normalize_header_cell(c) for c in _split_table_row(raw_rows[0])]
    findings: list[Finding] = []
    missing = [c for c in _REQUIRED_COLUMNS if c not in header_cells]
    if missing:
        findings.append(
            Finding(
                "HIGH",
                "verified-deps",
                plan_path,
                f"Verified Dependencies missing required columns: {missing}",
            )
        )
        return findings  # column shape broken — skip per-row checks

    data_rows = [r for r in raw_rows[1:] if not _SEPARATOR_ROW.match(r)]
    if not data_rows:
        return [
            Finding(
                "HIGH",
                "verified-deps",
                plan_path,
                "Verified Dependencies table has header but no data rows (declared empty)",
            )
        ]

    pkg_idx = header_cells.index("package")
    reg_idx = header_cells.index("registry")

    for raw in data_rows:
        cells = _split_table_row(raw)
        if len(cells) <= max(pkg_idx, reg_idx):
            findings.append(
                Finding(
                    "HIGH",
                    "verified-deps",
                    plan_path,
                    f"Verified Dependencies row underfilled: {raw!r}",
                )
            )
            continue
        package = _clean_scope_entry(cells[pkg_idx])
        registry = cells[reg_idx].lower()
        if not registry:
            findings.append(
                Finding(
                    "HIGH",
                    "verified-deps",
                    plan_path,
                    f"Verified Dependencies row missing registry: {raw!r}",
                )
            )
            continue
        if registry not in _KNOWN_ECOSYSTEMS:
            findings.append(
                Finding(
                    "HIGH",
                    "verified-deps",
                    plan_path,
                    f"unknown ecosystem {registry!r}",
                )
            )
            continue
        if check_registries:
            findings.extend(_probe_registry(plan_path, package, registry))

    return findings


def _probe_registry(plan_path: Path, package: str, registry: str) -> list[Finding]:
    """Shell out to the registry CLI; map exit/timeout/missing-CLI to findings."""
    cmd: list[str]
    if registry == "npm":
        cmd = ["npm", "view", package, "versions", "--json"]
    elif registry == "pypi":
        cmd = ["pip", "index", "versions", package]
    else:
        return [
            Finding(
                "WARN",
                "verified-deps",
                plan_path,
                f"registry probe not implemented for {registry}",
            )
        ]

    if shutil.which(cmd[0]) is None:
        return [
            Finding(
                "WARN",
                "verified-deps",
                plan_path,
                f"registry CLI {cmd[0]!r} not on PATH",
            )
        ]

    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=_REGISTRY_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return [
            Finding(
                "WARN",
                "verified-deps",
                plan_path,
                f"registry probe timed out for {package}",
            )
        ]

    if result.returncode != 0:
        return [
            Finding(
                "HIGH",
                "verified-deps",
                plan_path,
                f"package {package!r} not found in {registry}",
            )
        ]
    return []

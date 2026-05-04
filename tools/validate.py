"""Consolidated structural validator for IDD artifacts.

Per M3 spec §5.3.6: shipped checks are structural in P2a (frontmatter, capability
uniqueness, Constitution shape, delta shape, NR placement, repo health). Semantic
checks (scenario↔acceptance, plan task↔acceptance, anchors module-resolve,
deviation cross-ref, Verified Deps registry) ship in P2b.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Severity = Literal["BLOCK", "HIGH", "MEDIUM", "LOW", "WARN", "INFO"]
Target = Literal[
    "spec",
    "plan",
    "delta",
    "constitution",
    "ship",
    "health",
    "all",
    "capability-uniqueness",
]
EXIT_NONZERO_SEVERITIES: frozenset[Severity] = frozenset({"BLOCK", "HIGH"})


class ValidationError(RuntimeError):
    """Raised when validator setup fails (not when findings are produced)."""


@dataclass(frozen=True)
class Finding:
    """A single validator finding.

    Attributes:
        severity: see module-level Severity literal. Exit-affecting values are
            in `EXIT_NONZERO_SEVERITIES`; the rest are advisory.
        target: Which validator produced the finding.
        file: Repo-relative path to the file that triggered the finding.
        message: Human-readable description.
    """

    severity: Severity
    target: str
    file: Path
    message: str

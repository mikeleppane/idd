"""Finding dataclass + severity vocabulary. Internal package surface (M3 §5.3.6)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Severity = Literal["BLOCK", "HIGH", "MEDIUM", "LOW", "WARN", "INFO"]
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


def _finding_to_dict(finding: Finding) -> dict[str, str]:
    return {
        "severity": finding.severity,
        "target": finding.target,
        "file": str(finding.file),
        "message": finding.message,
    }

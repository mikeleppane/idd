"""IDD validator package.

Per M3 spec §5.3.6 D-8, P2a structural validators (frontmatter, capability
uniqueness, Constitution shape, delta shape, NR placement, repo health) and
P2b semantic validators (scenarios↔acceptance, anchors module-resolve,
plan-tasks↔acceptance, deviations↔decisions, Verified Deps registry) live
side by side here. `validate_health` delegates to other validators when
needed; delegated findings carry the **source** validator's `target` string
so provenance is preserved across the boundary (P2a follow-up #2, option A).
"""

from ._finding import (
    EXIT_NONZERO_SEVERITIES,
    Finding,
    Severity,
    ValidationError,
)
from .cli import main
from .constitution import validate_constitution
from .delta import validate_delta
from .health import validate_health
from .spec_semantic import validate_scenarios
from .spec_structural import (
    validate_capability_uniqueness,
    validate_frontmatter,
    validate_negative_requirements,
)

__all__ = [
    "EXIT_NONZERO_SEVERITIES",
    "Finding",
    "Severity",
    "ValidationError",
    "main",
    "validate_capability_uniqueness",
    "validate_constitution",
    "validate_delta",
    "validate_frontmatter",
    "validate_health",
    "validate_negative_requirements",
    "validate_scenarios",
]

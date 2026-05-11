"""FORGE validator package.

Per M3 spec §5.3.6 D-8, P2a structural validators (frontmatter, capability
uniqueness, Constitution shape, delta shape, NR placement, repo health) and
P2b semantic validators (scenarios↔acceptance, anchors module-resolve,
plan-tasks↔acceptance, deviations↔decisions, Verified Deps registry) live
side by side here. `validate_health` delegates to other validators when
needed; delegated findings carry the **source** validator's `target` string
so provenance is preserved across the boundary (P2a follow-up #2, option A).
"""

from ._config_shape import validate_config
from ._finding import (
    EXIT_NONZERO_SEVERITIES,
    MAX_FIX_HINT_LEN,
    Finding,
    Severity,
    ValidationError,
)
from ._research_shape import validate_research
from .cli import main
from .constitution import validate_constitution
from .delta import validate_delta
from .domain_glossary import validate_domain_glossary
from .health import validate_health
from .plan import validate_plan_tasks, validate_verified_deps
from .qa_shape import validate_qa_shape
from .spec_semantic import validate_anchors, validate_scenarios
from .spec_structural import (
    validate_capability_spec_sections,
    validate_capability_uniqueness,
    validate_frontmatter,
    validate_negative_requirements,
)
from .state_semantic import validate_deviations
from .tdd_evidence import validate_tdd_evidence

__all__ = [
    "EXIT_NONZERO_SEVERITIES",
    "MAX_FIX_HINT_LEN",
    "Finding",
    "Severity",
    "ValidationError",
    "main",
    "validate_anchors",
    "validate_capability_spec_sections",
    "validate_capability_uniqueness",
    "validate_config",
    "validate_constitution",
    "validate_delta",
    "validate_deviations",
    "validate_domain_glossary",
    "validate_frontmatter",
    "validate_health",
    "validate_negative_requirements",
    "validate_plan_tasks",
    "validate_qa_shape",
    "validate_research",
    "validate_scenarios",
    "validate_tdd_evidence",
    "validate_verified_deps",
]

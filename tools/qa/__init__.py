"""Black-box QA modules for the post-ship acceptance phase.

The QA layer runs ecosystem-agnostic checks against the merged artifact:
acceptance black-box review, adversarial probing, and Negative-Requirement
re-grep. Modules here have NO implementation context — only the SPEC.md and
shipped artifact descriptor — so findings stay in user-facing terms.
"""

from __future__ import annotations


class QAError(RuntimeError):
    """Raised when a QA module cannot proceed (missing inputs, malformed state)."""


# Re-export acceptance-module symbols at package level. The import lives below
# the QAError definition because :mod:`tools.qa.acceptance` imports QAError
# from this package — defining QAError first lets the submodule load cleanly.
from tools.qa.acceptance import (  # noqa: E402
    AcceptanceResult,
    ArtifactDescriptor,
    PromiseCheck,
    SpecPromise,
    parse_spec_promises,
    run_acceptance,
)

__all__ = [
    "AcceptanceResult",
    "ArtifactDescriptor",
    "PromiseCheck",
    "QAError",
    "SpecPromise",
    "parse_spec_promises",
    "run_acceptance",
]

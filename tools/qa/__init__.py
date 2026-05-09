"""Black-box QA modules for the post-ship acceptance phase.

The QA layer runs ecosystem-agnostic checks against the merged artifact:
adversarial probing and Negative-Requirement re-grep. Modules here have NO
implementation context — only the SPEC.md and shipped artifact path — so
findings stay in user-facing terms.
"""

from __future__ import annotations


class QAError(RuntimeError):
    """Raised when a QA module cannot proceed (missing inputs, malformed state)."""


__all__ = ["QAError"]

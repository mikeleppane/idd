"""Schema migration helpers.

Importing this package populates :data:`tools.migrations.registry.REGISTRY`
with the baseline v1 identity migrations for every known FORGE file kind, so
consumers can ``from tools.migrations.registry import REGISTRY`` (or any of
the helper functions) without first remembering to import ``v1_noop`` for its
side effect.
"""

from tools.migrations import v1_noop as _v1_noop

__all__ = ["_v1_noop"]

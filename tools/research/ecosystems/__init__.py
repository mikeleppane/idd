"""Registry of ecosystem detector plugins.

Each plugin module exposes a module-level ``plugin = MyEcosystem()``
instance satisfying :class:`tools.research.ecosystem.Ecosystem`. The
:data:`PLUGINS` list enumerates the live instances; the detector walks
this list in declaration order (output is then sorted by ``(priority,
name)`` so registration order does not leak into results).
"""

from tools.research.ecosystem import Ecosystem
from tools.research.ecosystems import generic

PLUGINS: list[Ecosystem] = [generic.plugin]

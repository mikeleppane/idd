"""Registry of ecosystem detector plugins.

Each plugin module exposes a module-level ``plugin = MyEcosystem()``
instance satisfying :class:`tools.research.ecosystem.Ecosystem`. The
:data:`PLUGINS` list enumerates the live instances; the detector walks
this list in declaration order (output is then sorted by ``(priority,
name)`` so registration order does not leak into results).
"""

from tools.research.ecosystem import Ecosystem
from tools.research.ecosystems import (
    dart,
    dotnet,
    elixir,
    generic,
    go,
    java,
    node,
    php,
    python,
    ruby,
    rust,
    swift,
)

PLUGINS: list[Ecosystem] = [
    python.plugin,
    node.plugin,
    rust.plugin,
    go.plugin,
    ruby.plugin,
    java.plugin,
    dotnet.plugin,
    elixir.plugin,
    php.plugin,
    swift.plugin,
    dart.plugin,
    generic.plugin,
]

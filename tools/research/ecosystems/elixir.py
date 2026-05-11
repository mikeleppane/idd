"""Elixir ecosystem plugin (mix.exs)."""

import re
from pathlib import Path

from tools.research.ecosystem import EcosystemRecord
from tools.research.ecosystems._walk import normalize_dep, scan_with_regex

_DEP_RE = re.compile(r"\{:([a-z_][a-z0-9_]*)\s*,")
_IMPORT_RE = re.compile(
    r"^\s*(?:import|alias|use)\s+([A-Z][A-Za-z0-9_.]+)",
    re.MULTILINE,
)


class ElixirEcosystem:
    """Detect an Elixir project via mix.exs."""

    name: str = "elixir"
    priority: int = 10

    def manifest_paths(self) -> tuple[str, ...]:
        """Return the canonical Elixir manifest filename."""
        return ("mix.exs",)

    def standard_dirs(self) -> dict[str, tuple[str, ...]]:
        """Return canonical test/source directory names for Elixir projects."""
        return {"test": ("test/",), "source": ("lib/",)}

    def match(self, repo_root: Path) -> EcosystemRecord | None:
        """Return a populated record when ``mix.exs`` is present."""
        if not (repo_root / "mix.exs").is_file():
            return None
        return EcosystemRecord(
            name=self.name,
            priority=self.priority,
            manifest_paths=self.manifest_paths(),
            declared_deps=self.declared_deps(repo_root),
            standard_dirs=self.standard_dirs(),
        )

    def declared_deps(self, repo_root: Path) -> tuple[str, ...]:
        """Return normalised Hex package names referenced in ``mix.exs``."""
        path = repo_root / "mix.exs"
        if not path.is_file():
            return ()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return ()
        out: dict[str, None] = {}
        for match in _DEP_RE.finditer(text):
            out.setdefault(normalize_dep(match.group(1)), None)
        return tuple(out)

    def scan_imports(self, repo_root: Path) -> list[str]:
        """Return lowercase module references from Elixir source files."""
        return list(scan_with_regex(repo_root, (".ex", ".exs"), _IMPORT_RE))


plugin = ElixirEcosystem()

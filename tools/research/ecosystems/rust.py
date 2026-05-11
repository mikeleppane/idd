"""Rust ecosystem plugin (Cargo.toml)."""

import re
import tomllib
from pathlib import Path

from tools.research.ecosystem import EcosystemRecord
from tools.research.ecosystems._walk import normalize_dep, scan_with_regex

_USE_RE = re.compile(r"^\s*use\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.MULTILINE)


class RustEcosystem:
    """Detect a Rust project via Cargo.toml."""

    name: str = "rust"
    priority: int = 10

    def manifest_paths(self) -> tuple[str, ...]:
        """Return the canonical Rust manifest filename."""
        return ("Cargo.toml",)

    def standard_dirs(self) -> dict[str, tuple[str, ...]]:
        """Return canonical test/source directory names for Rust projects."""
        return {"test": ("tests/",), "source": ("src/",)}

    def match(self, repo_root: Path) -> EcosystemRecord | None:
        """Return a populated record when ``Cargo.toml`` is present."""
        if not (repo_root / "Cargo.toml").is_file():
            return None
        return EcosystemRecord(
            name=self.name,
            priority=self.priority,
            manifest_paths=self.manifest_paths(),
            declared_deps=self.declared_deps(repo_root),
            standard_dirs=self.standard_dirs(),
        )

    def declared_deps(self, repo_root: Path) -> tuple[str, ...]:
        """Return normalised crate names from ``[dependencies]`` + ``[dev-dependencies]``."""
        path = repo_root / "Cargo.toml"
        if not path.is_file():
            return ()
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return ()
        out: dict[str, None] = {}
        for key in ("dependencies", "dev-dependencies", "build-dependencies"):
            section = data.get(key, {})
            if isinstance(section, dict):
                for name in section:
                    if isinstance(name, str) and name:
                        out.setdefault(normalize_dep(name), None)
        return tuple(out)

    def scan_imports(self, repo_root: Path) -> list[str]:
        """Return lowercase top-level crate names found in Rust ``use`` statements."""
        return list(scan_with_regex(repo_root, (".rs",), _USE_RE))


plugin = RustEcosystem()

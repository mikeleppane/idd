"""Dart ecosystem plugin (pubspec.yaml)."""

import re
from pathlib import Path

import yaml

from tools.research.ecosystem import EcosystemRecord
from tools.research.ecosystems._walk import normalize_dep, scan_with_regex

_IMPORT_RE = re.compile(r"^\s*import\s+'package:([^/']+)/", re.MULTILINE)


class DartEcosystem:
    """Detect a Dart / Flutter project via pubspec.yaml."""

    name: str = "dart"
    priority: int = 10

    def manifest_paths(self) -> tuple[str, ...]:
        """Return the canonical Dart manifest filename."""
        return ("pubspec.yaml",)

    def standard_dirs(self) -> dict[str, tuple[str, ...]]:
        """Return canonical test/source directory names for Dart/Flutter projects."""
        return {"test": ("test/",), "source": ("lib/",)}

    def match(self, repo_root: Path) -> EcosystemRecord | None:
        """Return a populated record when ``pubspec.yaml`` is present."""
        if not (repo_root / "pubspec.yaml").is_file():
            return None
        return EcosystemRecord(
            name=self.name,
            priority=self.priority,
            manifest_paths=self.manifest_paths(),
            declared_deps=self.declared_deps(repo_root),
            standard_dirs=self.standard_dirs(),
        )

    def declared_deps(self, repo_root: Path) -> tuple[str, ...]:
        """Return normalised Dart package names declared in ``pubspec.yaml``."""
        path = repo_root / "pubspec.yaml"
        if not path.is_file():
            return ()
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            return ()
        if not isinstance(data, dict):
            return ()
        out: dict[str, None] = {}
        for key in ("dependencies", "dev_dependencies"):
            section = data.get(key, {})
            if isinstance(section, dict):
                for name in section:
                    if isinstance(name, str) and name and name != "flutter":
                        out.setdefault(normalize_dep(name), None)
        return tuple(out)

    def scan_imports(self, repo_root: Path) -> list[str]:
        """Return lowercase package names found in Dart ``import 'package:...'`` lines."""
        return list(scan_with_regex(repo_root, (".dart",), _IMPORT_RE))


plugin = DartEcosystem()

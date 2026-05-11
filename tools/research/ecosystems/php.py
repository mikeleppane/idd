"""PHP ecosystem plugin (composer.json)."""

import json
import re
from pathlib import Path

from tools.research.ecosystem import EcosystemRecord
from tools.research.ecosystems._walk import normalize_dep, scan_with_regex

_USE_RE = re.compile(r"^\s*use\s+([A-Za-z0-9_\\]+)\s*(?:as\s+\w+)?\s*;", re.MULTILINE)


class PhpEcosystem:
    """Detect a PHP project via composer.json."""

    name: str = "php"
    priority: int = 10

    def manifest_paths(self) -> tuple[str, ...]:
        """Return the canonical PHP manifest filename."""
        return ("composer.json",)

    def standard_dirs(self) -> dict[str, tuple[str, ...]]:
        """Return canonical test/source directory names for PHP projects."""
        return {"test": ("tests/", "test/"), "source": ("src/", "app/")}

    def match(self, repo_root: Path) -> EcosystemRecord | None:
        """Return a populated record when ``composer.json`` is present."""
        if not (repo_root / "composer.json").is_file():
            return None
        return EcosystemRecord(
            name=self.name,
            priority=self.priority,
            manifest_paths=self.manifest_paths(),
            declared_deps=self.declared_deps(repo_root),
            standard_dirs=self.standard_dirs(),
        )

    def declared_deps(self, repo_root: Path) -> tuple[str, ...]:
        """Return normalised Composer package names from ``require`` + ``require-dev``."""
        path = repo_root / "composer.json"
        if not path.is_file():
            return ()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ()
        if not isinstance(data, dict):
            return ()
        out: dict[str, None] = {}
        for key in ("require", "require-dev"):
            section = data.get(key, {})
            if isinstance(section, dict):
                for name in section:
                    if isinstance(name, str) and name and name != "php":
                        out.setdefault(normalize_dep(name), None)
        return tuple(out)

    def scan_imports(self, repo_root: Path) -> list[str]:
        """Return lowercase namespace references from ``use`` statements in PHP files."""
        return list(scan_with_regex(repo_root, (".php",), _USE_RE))


plugin = PhpEcosystem()

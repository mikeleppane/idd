"""Swift ecosystem plugin (Package.swift)."""

import re
from pathlib import Path

from tools.research.ecosystem import EcosystemRecord
from tools.research.ecosystems._walk import normalize_dep, scan_with_regex

_PACKAGE_RE = re.compile(r"""\.package\([^)]*name:\s*"([^"]+)\"""")
_PACKAGE_URL_RE = re.compile(r"""\.package\([^)]*url:\s*"([^"]+)\"""")
_IMPORT_RE = re.compile(r"^\s*import\s+([A-Za-z0-9_]+)", re.MULTILINE)


class SwiftEcosystem:
    """Detect a Swift project via Package.swift."""

    name: str = "swift"
    priority: int = 10

    def manifest_paths(self) -> tuple[str, ...]:
        """Return the canonical Swift manifest filename."""
        return ("Package.swift",)

    def standard_dirs(self) -> dict[str, tuple[str, ...]]:
        """Return canonical test/source directory names for Swift projects."""
        return {"test": ("Tests/",), "source": ("Sources/",)}

    def match(self, repo_root: Path) -> EcosystemRecord | None:
        """Return a populated record when ``Package.swift`` is present."""
        if not (repo_root / "Package.swift").is_file():
            return None
        return EcosystemRecord(
            name=self.name,
            priority=self.priority,
            manifest_paths=self.manifest_paths(),
            declared_deps=self.declared_deps(repo_root),
            standard_dirs=self.standard_dirs(),
        )

    def declared_deps(self, repo_root: Path) -> tuple[str, ...]:
        """Return normalised Swift package names declared in ``Package.swift``."""
        path = repo_root / "Package.swift"
        if not path.is_file():
            return ()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return ()
        out: dict[str, None] = {}
        for match in _PACKAGE_RE.finditer(text):
            out.setdefault(normalize_dep(match.group(1)), None)
        for match in _PACKAGE_URL_RE.finditer(text):
            url = match.group(1)
            tail = url.rstrip("/").split("/")[-1]
            tail = tail.removesuffix(".git")
            if tail:
                out.setdefault(normalize_dep(tail), None)
        return tuple(out)

    def scan_imports(self, repo_root: Path) -> list[str]:
        """Return lowercase module names imported across ``.swift`` source files."""
        return list(scan_with_regex(repo_root, (".swift",), _IMPORT_RE))


plugin = SwiftEcosystem()

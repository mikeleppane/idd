""".NET ecosystem plugin (*.csproj / *.fsproj / *.vbproj / Directory.Packages.props)."""

import re
from pathlib import Path
from xml.etree import ElementTree as ET

from tools.research.ecosystem import EcosystemRecord
from tools.research.ecosystems._walk import normalize_dep, scan_with_regex

_USING_RE = re.compile(r"^\s*using\s+([a-zA-Z0-9_.]+)\s*;", re.MULTILINE)


class DotNetEcosystem:
    """Detect a .NET project via *.csproj / *.fsproj / *.vbproj / Directory.Packages.props."""

    name: str = "dotnet"
    priority: int = 10

    def manifest_paths(self) -> tuple[str, ...]:
        """Return the canonical .NET manifest globs."""
        return ("*.csproj", "*.fsproj", "*.vbproj", "Directory.Packages.props")

    def standard_dirs(self) -> dict[str, tuple[str, ...]]:
        """Return canonical test/source directory names for .NET projects."""
        return {"test": ("test/", "tests/"), "source": ("src/",)}

    def _present_manifests(self, repo_root: Path) -> tuple[str, ...]:
        present: list[str] = []
        for pattern in ("*.csproj", "*.fsproj", "*.vbproj"):
            present.extend(match.name for match in sorted(repo_root.glob(pattern)))
        if (repo_root / "Directory.Packages.props").is_file():
            present.append("Directory.Packages.props")
        return tuple(present)

    def match(self, repo_root: Path) -> EcosystemRecord | None:
        """Return a populated record when any .NET project file is at the root."""
        manifests = self._present_manifests(repo_root)
        if not manifests:
            return None
        return EcosystemRecord(
            name=self.name,
            priority=self.priority,
            manifest_paths=manifests,
            declared_deps=self.declared_deps(repo_root),
            standard_dirs=self.standard_dirs(),
        )

    def declared_deps(self, repo_root: Path) -> tuple[str, ...]:
        """Return normalised PackageReference Include= names from .NET project files."""
        out: dict[str, None] = {}
        try:
            paths: list[Path] = []
            for pattern in ("*.csproj", "*.fsproj", "*.vbproj"):
                paths.extend(sorted(repo_root.glob(pattern)))
            props = repo_root / "Directory.Packages.props"
            if props.is_file():
                paths.append(props)
            for path in paths:
                self._read_project(path, out)
        except (OSError, ET.ParseError):
            return ()
        return tuple(out)

    def _read_project(self, path: Path, sink: dict[str, None]) -> None:
        try:
            text = path.read_text(encoding="utf-8")
            root = ET.fromstring(text)  # noqa: S314  (developer-controlled input)
        except (OSError, ET.ParseError):
            return
        for elem in root.iter():
            if "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]
        for ref in root.iter("PackageReference"):
            include = ref.get("Include") or ref.get("Update")
            if include:
                sink.setdefault(normalize_dep(include), None)

    def scan_imports(self, repo_root: Path) -> list[str]:
        """Return lowercase namespaces referenced via ``using`` directives."""
        return list(scan_with_regex(repo_root, (".cs", ".fs", ".vb"), _USING_RE))


plugin = DotNetEcosystem()

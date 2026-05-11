"""Java ecosystem plugin (Maven / Gradle)."""

import re
from pathlib import Path
from xml.etree import ElementTree as ET

from tools.research.ecosystem import EcosystemRecord
from tools.research.ecosystems._walk import normalize_dep, scan_with_regex

_GRADLE_DEP_RE = re.compile(
    r"""(?:implementation|api|compile|testImplementation|runtimeOnly)\s*[(\s]\s*['"]([^'"]+)['"]"""
)
_IMPORT_RE = re.compile(r"^\s*import\s+(?:static\s+)?([a-zA-Z0-9_.]+)\s*;", re.MULTILINE)
_GRADLE_COORD_MIN_PARTS = 2  # group:artifact at minimum


class JavaEcosystem:
    """Detect a Java project via pom.xml or Gradle build files."""

    name: str = "java"
    priority: int = 10

    def manifest_paths(self) -> tuple[str, ...]:
        """Return the canonical Java manifest filenames."""
        return ("pom.xml", "build.gradle", "build.gradle.kts")

    def standard_dirs(self) -> dict[str, tuple[str, ...]]:
        """Return canonical test/source directory names for Java projects."""
        return {"test": ("src/test/java/",), "source": ("src/main/java/",)}

    def _present_manifests(self, repo_root: Path) -> tuple[str, ...]:
        return tuple(
            name
            for name in ("pom.xml", "build.gradle", "build.gradle.kts")
            if (repo_root / name).is_file()
        )

    def match(self, repo_root: Path) -> EcosystemRecord | None:
        """Return a populated record when a Maven or Gradle manifest is present."""
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
        """Return normalised artifactIds + Gradle coordinates declared in build files."""
        out: dict[str, None] = {}
        try:
            pom = repo_root / "pom.xml"
            if pom.is_file():
                self._read_pom(pom, out)
            for gradle_name in ("build.gradle", "build.gradle.kts"):
                gradle = repo_root / gradle_name
                if gradle.is_file():
                    text = gradle.read_text(encoding="utf-8")
                    for match in _GRADLE_DEP_RE.finditer(text):
                        coord = match.group(1)
                        # Gradle coords are group:artifact:version — pick artifact.
                        parts = coord.split(":")
                        if len(parts) >= _GRADLE_COORD_MIN_PARTS:
                            out.setdefault(normalize_dep(parts[1]), None)
                        else:
                            out.setdefault(normalize_dep(coord), None)
        except (OSError, ET.ParseError):
            return ()
        return tuple(out)

    def _read_pom(self, pom: Path, sink: dict[str, None]) -> None:
        # `defusedxml` would be safer but isn't vendored; pom.xml ships with
        # the repo so the threat model is "developer-controlled input".
        text = pom.read_text(encoding="utf-8")
        try:
            root = ET.fromstring(text)  # noqa: S314  (developer-controlled input)
        except ET.ParseError:
            return
        # Strip XML namespaces so we can find tags by local name.
        for elem in root.iter():
            if "}" in elem.tag:
                elem.tag = elem.tag.split("}", 1)[1]
        # Walk only ``<dependency>`` elements (covers ``<dependencies>`` and
        # ``<dependencyManagement><dependencies>``). Iterating every
        # ``<artifactId>`` would also pull the project's own + ``<parent>``
        # coordinates, which are not declared deps.
        for dep in root.iter("dependency"):
            artifact = dep.find("artifactId")
            if artifact is None:
                continue
            text_value = (artifact.text or "").strip()
            if text_value:
                sink.setdefault(normalize_dep(text_value), None)

    def scan_imports(self, repo_root: Path) -> list[str]:
        """Return lowercase imported package paths from ``.java``/``.kt`` files."""
        return list(scan_with_regex(repo_root, (".java", ".kt", ".kts"), _IMPORT_RE))


plugin = JavaEcosystem()

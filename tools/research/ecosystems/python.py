"""Python ecosystem plugin (pyproject.toml / requirements*.txt / setup.{py,cfg})."""

import re
import tomllib
from pathlib import Path

from tools.research.ecosystem import EcosystemRecord
from tools.research.ecosystems._walk import normalize_dep, scan_with_regex

_REQ_LINE_RE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)")
_IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.MULTILINE)


class PythonEcosystem:
    """Detect a Python project and surface its declared deps + import scan."""

    name: str = "python"
    priority: int = 10

    def manifest_paths(self) -> tuple[str, ...]:
        """Return the canonical Python manifest filenames."""
        return ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")

    def standard_dirs(self) -> dict[str, tuple[str, ...]]:
        """Return canonical test/source directory names for Python projects."""
        return {"test": ("tests/", "test/"), "source": ("src/", "")}

    def _present_manifests(self, repo_root: Path) -> tuple[str, ...]:
        present: list[str] = [
            name
            for name in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt")
            if (repo_root / name).is_file()
        ]
        present.extend(extra.name for extra in sorted(repo_root.glob("requirements-*.txt")))
        return tuple(present)

    def match(self, repo_root: Path) -> EcosystemRecord | None:
        """Return a populated record when any Python manifest is present."""
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
        """Return normalised dependency names from pyproject + requirements files."""
        deps: dict[str, None] = {}
        try:
            self._collect_pyproject(repo_root, deps)
            self._collect_requirements_files(repo_root, deps)
        except (OSError, tomllib.TOMLDecodeError, ValueError):
            return ()
        return tuple(deps)

    def _collect_pyproject(self, repo_root: Path, deps: dict[str, None]) -> None:
        pyproject = repo_root / "pyproject.toml"
        if not pyproject.is_file():
            return
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        project = data.get("project", {})
        if not isinstance(project, dict):
            return
        for raw in project.get("dependencies", []) or []:
            if isinstance(raw, str):
                self._add_requirement(raw, deps)
        optional = project.get("optional-dependencies", {}) or {}
        if isinstance(optional, dict):
            for group in optional.values():
                if isinstance(group, list):
                    for raw in group:
                        if isinstance(raw, str):
                            self._add_requirement(raw, deps)

    def _collect_requirements_files(self, repo_root: Path, deps: dict[str, None]) -> None:
        files = [repo_root / "requirements.txt", *sorted(repo_root.glob("requirements-*.txt"))]
        for req_file in files:
            if not req_file.is_file():
                continue
            for line in req_file.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith(("#", "-")):
                    continue
                self._add_requirement(stripped, deps)

    def _add_requirement(self, raw: str, sink: dict[str, None]) -> None:
        match = _REQ_LINE_RE.match(raw)
        if not match:
            return
        normalised = normalize_dep(match.group(1))
        if normalised:
            sink.setdefault(normalised, None)

    def scan_imports(self, repo_root: Path) -> list[str]:
        """Return lowercase top-level module names imported anywhere in the repo."""
        return list(scan_with_regex(repo_root, (".py",), _IMPORT_RE))


plugin = PythonEcosystem()

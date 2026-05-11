"""Ruby ecosystem plugin (Gemfile / *.gemspec)."""

import re
from pathlib import Path

from tools.research.ecosystem import EcosystemRecord
from tools.research.ecosystems._walk import normalize_dep, scan_with_regex

_GEM_LINE_RE = re.compile(r"""^\s*gem\s+['"]([^'"]+)['"]""")
_ADD_DEP_RE = re.compile(r"""add_(?:runtime_|development_)?dependency\s+['"]([^'"]+)['"]""")
_REQUIRE_RE = re.compile(r"""^\s*require\s+['"]([^'"]+)['"]""", re.MULTILINE)


class RubyEcosystem:
    """Detect a Ruby project via Gemfile or gemspec."""

    name: str = "ruby"
    priority: int = 10

    def manifest_paths(self) -> tuple[str, ...]:
        """Return the canonical Ruby manifest filenames."""
        return ("Gemfile", "*.gemspec")

    def standard_dirs(self) -> dict[str, tuple[str, ...]]:
        """Return canonical test/source directory names for Ruby projects."""
        return {"test": ("spec/", "test/"), "source": ("lib/", "app/")}

    def _present_manifests(self, repo_root: Path) -> tuple[str, ...]:
        present: list[str] = []
        if (repo_root / "Gemfile").is_file():
            present.append("Gemfile")
        present.extend(gemspec.name for gemspec in sorted(repo_root.glob("*.gemspec")))
        return tuple(present)

    def match(self, repo_root: Path) -> EcosystemRecord | None:
        """Return a populated record when a Gemfile or *.gemspec is present."""
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
        """Return normalised gem names declared in Gemfile + gemspec files."""
        out: dict[str, None] = {}
        try:
            gemfile = repo_root / "Gemfile"
            if gemfile.is_file():
                for line in gemfile.read_text(encoding="utf-8").splitlines():
                    match = _GEM_LINE_RE.match(line)
                    if match:
                        out.setdefault(normalize_dep(match.group(1)), None)
            for gemspec in sorted(repo_root.glob("*.gemspec")):
                text = gemspec.read_text(encoding="utf-8")
                for match in _ADD_DEP_RE.finditer(text):
                    out.setdefault(normalize_dep(match.group(1)), None)
        except OSError:
            return ()
        return tuple(out)

    def scan_imports(self, repo_root: Path) -> list[str]:
        """Return lowercase library names discovered in Ruby ``require`` calls."""
        return list(scan_with_regex(repo_root, (".rb",), _REQUIRE_RE))


plugin = RubyEcosystem()

"""Node.js ecosystem plugin (package.json)."""

import json
import re
from pathlib import Path

from tools.research.ecosystem import EcosystemRecord
from tools.research.ecosystems._walk import normalize_dep, scan_with_regex

_IMPORT_RE = re.compile(r"""(?:require|from)\s+['"]([^'"]+)['"]""")


class NodeEcosystem:
    """Detect a Node.js project via package.json."""

    name: str = "node"
    priority: int = 10

    def manifest_paths(self) -> tuple[str, ...]:
        """Return the canonical Node manifest filename."""
        return ("package.json",)

    def standard_dirs(self) -> dict[str, tuple[str, ...]]:
        """Return canonical test/source directory names for Node projects."""
        return {"test": ("test/", "tests/", "__tests__/"), "source": ("src/", "lib/")}

    def match(self, repo_root: Path) -> EcosystemRecord | None:
        """Return a populated record when ``package.json`` is present."""
        if not (repo_root / "package.json").is_file():
            return None
        return EcosystemRecord(
            name=self.name,
            priority=self.priority,
            manifest_paths=self.manifest_paths(),
            declared_deps=self.declared_deps(repo_root),
            standard_dirs=self.standard_dirs(),
        )

    def declared_deps(self, repo_root: Path) -> tuple[str, ...]:
        """Return normalised dependency + devDependency names from package.json."""
        path = repo_root / "package.json"
        if not path.is_file():
            return ()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return ()
        if not isinstance(data, dict):
            return ()
        out: dict[str, None] = {}
        for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            section = data.get(key, {})
            if isinstance(section, dict):
                for name in section:
                    if isinstance(name, str) and name:
                        # Strip leading "@scope/" for normalised display.
                        stripped = name.lstrip("@")
                        out.setdefault(normalize_dep(stripped), None)
        return tuple(out)

    def scan_imports(self, repo_root: Path) -> list[str]:
        """Return lowercase module specifiers found in JS/TS imports."""
        raw = scan_with_regex(repo_root, (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"), _IMPORT_RE)
        # Drop relative imports (./ ../) — they are not packages.
        return [name for name in raw if not name.startswith((".", "/"))]


plugin = NodeEcosystem()

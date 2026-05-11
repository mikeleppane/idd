"""Go ecosystem plugin (go.mod)."""

import re
from pathlib import Path

from tools.research.ecosystem import EcosystemRecord
from tools.research.ecosystems._walk import normalize_dep, scan_with_regex

_REQUIRE_LINE_RE = re.compile(r"^\s*([a-zA-Z0-9._/\-]+)\s+v[0-9]")
_IMPORT_QUOTED_RE = re.compile(r"""^\s*"([^"]+)"\s*$""", re.MULTILINE)
_IMPORT_INLINE_RE = re.compile(r"""import\s+"([^"]+)\"""")


class GoEcosystem:
    """Detect a Go project via go.mod."""

    name: str = "go"
    priority: int = 10

    def manifest_paths(self) -> tuple[str, ...]:
        """Return the canonical Go manifest filename."""
        return ("go.mod",)

    def standard_dirs(self) -> dict[str, tuple[str, ...]]:
        """Return canonical test/source directory names for Go projects."""
        return {"test": ("",), "source": ("cmd/", "pkg/", "internal/", "")}

    def match(self, repo_root: Path) -> EcosystemRecord | None:
        """Return a populated record when ``go.mod`` is present."""
        if not (repo_root / "go.mod").is_file():
            return None
        return EcosystemRecord(
            name=self.name,
            priority=self.priority,
            manifest_paths=self.manifest_paths(),
            declared_deps=self.declared_deps(repo_root),
            standard_dirs=self.standard_dirs(),
        )

    def declared_deps(self, repo_root: Path) -> tuple[str, ...]:
        """Return normalised module paths declared in ``go.mod`` ``require`` blocks."""
        path = repo_root / "go.mod"
        if not path.is_file():
            return ()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return ()
        out: dict[str, None] = {}
        in_block = False
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.startswith("require ("):
                in_block = True
                continue
            if in_block:
                if line.startswith(")"):
                    in_block = False
                    continue
                self._maybe_add(line, out)
            elif line.startswith("require "):
                self._maybe_add(line.removeprefix("require ").strip(), out)
        return tuple(out)

    def _maybe_add(self, line: str, sink: dict[str, None]) -> None:
        if not line or line.startswith("//"):
            return
        match = _REQUIRE_LINE_RE.match(line)
        if not match:
            return
        sink.setdefault(normalize_dep(match.group(1)), None)

    def scan_imports(self, repo_root: Path) -> list[str]:
        """Return lowercase import paths discovered across ``.go`` source files."""
        seen: dict[str, None] = {}
        for raw in scan_with_regex(repo_root, (".go",), _IMPORT_QUOTED_RE):
            seen.setdefault(raw, None)
        for raw in scan_with_regex(repo_root, (".go",), _IMPORT_INLINE_RE):
            seen.setdefault(raw, None)
        return list(seen)


plugin = GoEcosystem()

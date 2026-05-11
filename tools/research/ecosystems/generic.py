"""Generic-ecosystem fallback plugin.

This plugin always matches and returns an :class:`EcosystemRecord` with
empty manifest/dep tuples. The detector returns it when no concrete
language plugin matched (and the caller did not pin a specific subset).
The research subagent then knows the repo has no recognized package
manager and falls back to "describe the directory structure" prose
instead of "parse manifests".
"""

from pathlib import Path

from tools.research.ecosystem import EcosystemRecord


class GenericEcosystem:
    """Fallback ecosystem that matches every repo with priority 99."""

    name: str = "generic"

    def match(self, repo_root: Path) -> EcosystemRecord | None:
        """Return the generic record. Always matches; never inspects the repo."""
        del repo_root
        return EcosystemRecord(
            name=self.name,
            priority=99,
            manifest_paths=(),
            declared_deps=(),
            standard_dirs={"test": (), "source": ()},
        )

    def scan_imports(self, repo_root: Path) -> list[str]:
        """Return empty: generic fallback has no language to scan."""
        del repo_root
        return []


plugin = GenericEcosystem()

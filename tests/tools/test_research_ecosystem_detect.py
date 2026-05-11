"""Tests for the research ecosystem detector contract and registry walker.

Covers the Ecosystem Protocol shape (runtime-checkable membership), the
`detect()` walker's pin filtering, deterministic ordering of multi-match
results, and the generic-fallback safety net for empty repos.
"""

from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path

import pytest

from tools.research import ecosystem as ecosystem_mod
from tools.research.ecosystem import (
    Ecosystem,
    EcosystemRecord,
    detect,
)
from tools.research.ecosystems import generic as generic_plugin


@dataclass
class _StubPlugin:
    """Stub plugin for monkey-patching the registry in priority/pin tests."""

    name: str
    priority: int
    matches: bool = True

    def match(self, repo_root: Path) -> EcosystemRecord | None:
        if not self.matches:
            return None
        return EcosystemRecord(
            name=self.name,
            priority=self.priority,
            manifest_paths=(),
            declared_deps=(),
            standard_dirs={"test": (), "source": ()},
        )

    def scan_imports(self, repo_root: Path) -> list[str]:
        return []


@dataclass
class _StubMissingScanImports:
    """Stub class missing ``scan_imports`` to verify Protocol enforcement."""

    name: str = "broken"

    def match(self, repo_root: Path) -> EcosystemRecord | None:
        return None


def _make_repo(tmp_path: Path) -> Path:
    (tmp_path / "README.md").write_text("# sample repo\n", encoding="utf-8")
    return tmp_path


def test_empty_repo_returns_generic_fallback(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    records = detect(repo)
    assert len(records) == 1
    assert records[0].name == "generic"
    assert records[0].priority == 99
    assert records[0].manifest_paths == ()
    assert records[0].declared_deps == ()
    assert records[0].standard_dirs == {"test": (), "source": ()}


def test_pinned_generic_returns_only_generic(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    records = detect(repo, pinned=["generic"])
    assert [r.name for r in records] == ["generic"]


def test_pinned_unknown_silently_filters_to_known(tmp_path: Path) -> None:
    """Pinned names not in the registry are silently dropped (not an error).

    P4.2 will register python/node/etc., so a future caller's pin list
    may legitimately include names that exist in some configurations and
    not others. The walker filters down to known names then evaluates
    them. With an unknown-only pin, the result is the empty list — the
    generic fallback applies *only* when no pin is supplied.
    """
    repo = _make_repo(tmp_path)
    records = detect(repo, pinned=["python"])
    assert records == []


def test_duplicate_priorities_break_tie_alphabetically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo(tmp_path)
    plugins = [
        _StubPlugin(name="zeta", priority=10),
        _StubPlugin(name="alpha", priority=10),
        _StubPlugin(name="mid", priority=10),
        generic_plugin.plugin,
    ]
    monkeypatch.setattr(ecosystem_mod, "_load_plugins", lambda: plugins)
    records = detect(repo)
    names = [r.name for r in records]
    assert names == ["alpha", "mid", "zeta", "generic"]


def test_priority_sort_dominates_name_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _make_repo(tmp_path)
    plugins = [
        _StubPlugin(name="zlow", priority=1),
        _StubPlugin(name="ahigh", priority=50),
        generic_plugin.plugin,
    ]
    monkeypatch.setattr(ecosystem_mod, "_load_plugins", lambda: plugins)
    records = detect(repo)
    assert [r.name for r in records] == ["zlow", "ahigh", "generic"]


def test_non_matching_plugins_dropped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _make_repo(tmp_path)
    plugins = [
        _StubPlugin(name="never", priority=5, matches=False),
        generic_plugin.plugin,
    ]
    monkeypatch.setattr(ecosystem_mod, "_load_plugins", lambda: plugins)
    records = detect(repo)
    assert [r.name for r in records] == ["generic"]


def test_ecosystem_protocol_runtime_checkable_accepts_stub() -> None:
    stub = _StubPlugin(name="alpha", priority=10)
    assert isinstance(stub, Ecosystem)


def test_ecosystem_protocol_rejects_class_missing_scan_imports() -> None:
    broken = _StubMissingScanImports()
    assert not isinstance(broken, Ecosystem)


def test_generic_plugin_scan_imports_returns_empty(tmp_path: Path) -> None:
    assert generic_plugin.plugin.scan_imports(tmp_path) == []


def test_ecosystem_record_is_frozen() -> None:
    record = EcosystemRecord(
        name="generic",
        priority=99,
        manifest_paths=(),
        declared_deps=(),
        standard_dirs={"test": (), "source": ()},
    )
    with pytest.raises(FrozenInstanceError):
        record.name = "changed"  # type: ignore[misc]


def test_pinned_preserves_priority_sort(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _make_repo(tmp_path)
    plugins = [
        _StubPlugin(name="alpha", priority=20),
        _StubPlugin(name="beta", priority=5),
        generic_plugin.plugin,
    ]
    monkeypatch.setattr(ecosystem_mod, "_load_plugins", lambda: plugins)
    records = detect(repo, pinned=["alpha", "beta"])
    assert [r.name for r in records] == ["beta", "alpha"]

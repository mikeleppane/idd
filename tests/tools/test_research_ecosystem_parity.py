"""Cross-plugin parity tests for the ecosystem detector registry.

These tests run uniformly over every entry in ``PLUGINS`` so adding a
twelfth ecosystem plugin in the future automatically gains parity
coverage. The shape contract checked here is the public surface every
plugin promises:

* :class:`Ecosystem` Protocol membership (runtime-checkable).
* ``match()`` returns ``None`` on a known-empty repo (the generic
  fallback is the explicit exception — it always matches).
* ``match()`` on the plugin's own happy fixture returns a populated
  :class:`EcosystemRecord` with all-lowercase normalised data.
* ``standard_dirs()`` exposes ``"test"`` + ``"source"`` as tuples.
"""

import pytest

from tools.research.ecosystem import Ecosystem, EcosystemRecord
from tools.research.ecosystems import PLUGINS

from .research_plugin_helpers import EMPTY_REPO_ROOT, fixture_path


@pytest.mark.parametrize("plugin", PLUGINS, ids=[p.name for p in PLUGINS])
def test_plugin_satisfies_ecosystem_protocol(plugin: Ecosystem) -> None:
    assert isinstance(plugin, Ecosystem)


@pytest.mark.parametrize("plugin", PLUGINS, ids=[p.name for p in PLUGINS])
def test_plugin_match_on_empty_repo(plugin: Ecosystem) -> None:
    record = plugin.match(EMPTY_REPO_ROOT)
    if plugin.name == "generic":
        assert record is not None
        assert record.name == "generic"
    else:
        assert record is None


@pytest.mark.parametrize(
    "plugin",
    [p for p in PLUGINS if p.name != "generic"],
    ids=[p.name for p in PLUGINS if p.name != "generic"],
)
def test_plugin_match_on_own_happy_fixture(plugin: Ecosystem) -> None:
    record = plugin.match(fixture_path(plugin.name, "happy"))
    assert isinstance(record, EcosystemRecord)
    assert record.name == plugin.name
    assert record.priority < 99
    assert record.manifest_paths != ()


@pytest.mark.parametrize(
    "plugin",
    [p for p in PLUGINS if p.name != "generic"],
    ids=[p.name for p in PLUGINS if p.name != "generic"],
)
def test_declared_deps_are_tuple_of_lowercase_strings(plugin: Ecosystem) -> None:
    deps = plugin.declared_deps(fixture_path(plugin.name, "happy"))  # type: ignore[attr-defined]
    assert isinstance(deps, tuple)
    for dep in deps:
        assert isinstance(dep, str)
        assert dep == dep.lower()
        assert "-" not in dep, f"{plugin.name}: hyphen leaked in {dep!r}"


@pytest.mark.parametrize("plugin", PLUGINS, ids=[p.name for p in PLUGINS])
def test_scan_imports_returns_lowercase_strings(plugin: Ecosystem) -> None:
    fixture = fixture_path(plugin.name, "happy") if plugin.name != "generic" else EMPTY_REPO_ROOT
    imports = plugin.scan_imports(fixture)
    assert isinstance(imports, list)
    for name in imports:
        assert isinstance(name, str)
        assert name == name.lower()


@pytest.mark.parametrize(
    "plugin",
    [p for p in PLUGINS if p.name != "generic"],
    ids=[p.name for p in PLUGINS if p.name != "generic"],
)
def test_standard_dirs_shape(plugin: Ecosystem) -> None:
    dirs = plugin.standard_dirs()  # type: ignore[attr-defined]
    assert isinstance(dirs, dict)
    assert set(dirs.keys()) == {"test", "source"}
    for value in dirs.values():
        assert isinstance(value, tuple)
        for entry in value:
            assert isinstance(entry, str)

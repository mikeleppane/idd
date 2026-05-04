"""Detect BDD frameworks per design §6.6 — pure file inspection, no transitive scanning.

The detector returns a ``Detected | Ambiguous | NotDetected`` tagged union so
the calling skill can distinguish "no signal" from "partial signal — ask once".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.bdd_detect import (
    Ambiguous,
    BDDFramework,
    Detected,
    NotDetected,
    detect,
)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_detects_pytest_bdd_when_dep_and_features_dir_present(tmp_path: Path) -> None:
    _write_text(
        tmp_path / "pyproject.toml",
        "[project]\nname = 'demo'\ndependencies = ['pytest-bdd']\n",
    )
    (tmp_path / "tests" / "features").mkdir(parents=True)
    result = detect(tmp_path)
    assert result == Detected(
        framework=BDDFramework(
            ecosystem="python",
            framework="pytest-bdd",
            features_dir=Path("tests/features"),
        ),
    )


def test_python_no_pytest_bdd_dep_returns_not_detected(tmp_path: Path) -> None:
    _write_text(tmp_path / "pyproject.toml", "[project]\nname = 'demo'\ndependencies = []\n")
    (tmp_path / "tests" / "features").mkdir(parents=True)
    assert detect(tmp_path) == NotDetected()


def test_python_pytest_bdd_dep_but_no_features_dir_returns_ambiguous(tmp_path: Path) -> None:
    _write_text(
        tmp_path / "pyproject.toml",
        "[project]\nname = 'demo'\ndependencies = ['pytest-bdd']\n",
    )
    result = detect(tmp_path)
    assert isinstance(result, Ambiguous)
    assert "python" in result.reason
    assert "tests/features" in result.reason


def test_detects_cucumber_node(tmp_path: Path) -> None:
    _write_text(
        tmp_path / "package.json",
        json.dumps({"devDependencies": {"@cucumber/cucumber": "^10.0.0"}}),
    )
    _write_text(tmp_path / "cucumber.cjs", "module.exports = {}\n")
    (tmp_path / "features").mkdir()
    result = detect(tmp_path)
    assert result == Detected(
        framework=BDDFramework(
            ecosystem="node",
            framework="cucumber-js",
            features_dir=Path("features"),
        ),
    )


def test_node_cucumber_dep_without_config_returns_ambiguous(tmp_path: Path) -> None:
    _write_text(
        tmp_path / "package.json",
        json.dumps({"dependencies": {"@cucumber/cucumber": "^10.0.0"}}),
    )
    (tmp_path / "features").mkdir()
    result = detect(tmp_path)
    assert isinstance(result, Ambiguous)
    assert "cucumber" in result.reason


def test_idd_config_override_wins(tmp_path: Path) -> None:
    (tmp_path / ".idd").mkdir()
    _write_text(
        tmp_path / ".idd" / "config.json",
        json.dumps(
            {
                "bdd_framework": {
                    "ecosystem": "other",
                    "framework": "custom-bdd",
                    "features_dir": "spec/bdd",
                }
            }
        ),
    )
    result = detect(tmp_path)
    assert result == Detected(
        framework=BDDFramework(
            ecosystem="other",
            framework="custom-bdd",
            features_dir=Path("spec/bdd"),
        ),
    )


def test_idd_config_override_rejects_absolute_features_dir(tmp_path: Path) -> None:
    (tmp_path / ".idd").mkdir()
    _write_text(
        tmp_path / ".idd" / "config.json",
        json.dumps(
            {
                "bdd_framework": {
                    "ecosystem": "python",
                    "framework": "pytest-bdd",
                    "features_dir": "/etc/passwd",
                }
            }
        ),
    )
    result = detect(tmp_path)
    assert isinstance(result, Ambiguous)
    assert "features_dir" in result.reason


def test_idd_config_override_rejects_dotdot_traversal(tmp_path: Path) -> None:
    (tmp_path / ".idd").mkdir()
    _write_text(
        tmp_path / ".idd" / "config.json",
        json.dumps(
            {
                "bdd_framework": {
                    "ecosystem": "python",
                    "framework": "pytest-bdd",
                    "features_dir": "../escape/features",
                }
            }
        ),
    )
    result = detect(tmp_path)
    assert isinstance(result, Ambiguous)
    assert "'..'" in result.reason


def test_idd_config_override_with_invalid_json_is_ambiguous(tmp_path: Path) -> None:
    (tmp_path / ".idd").mkdir()
    _write_text(tmp_path / ".idd" / "config.json", "{not json")
    result = detect(tmp_path)
    assert isinstance(result, Ambiguous)
    assert "valid JSON" in result.reason


def test_idd_config_override_missing_keys_is_ambiguous(tmp_path: Path) -> None:
    (tmp_path / ".idd").mkdir()
    _write_text(
        tmp_path / ".idd" / "config.json",
        json.dumps({"bdd_framework": {"ecosystem": "python"}}),
    )
    result = detect(tmp_path)
    assert isinstance(result, Ambiguous)
    assert "missing key" in result.reason


def test_no_signals_returns_not_detected(tmp_path: Path) -> None:
    assert detect(tmp_path) == NotDetected()


def test_transitive_dep_in_lockfile_does_not_trigger(tmp_path: Path) -> None:
    """False positives are worse than missed escalations (design §6.6)."""
    _write_text(tmp_path / "pyproject.toml", "[project]\nname = 'demo'\ndependencies = []\n")
    _write_text(
        tmp_path / "uv.lock",
        "[[package]]\nname = 'pytest-bdd'\nversion = '7.0.0'\n",
    )
    (tmp_path / "tests" / "features").mkdir(parents=True)
    assert detect(tmp_path) == NotDetected()


@pytest.mark.parametrize(
    "ecosystem_file,marker",
    [
        ("Gemfile", "gem 'cucumber'"),
        ("go.mod", "require github.com/cucumber/godog v0.13.0"),
    ],
)
def test_other_ecosystems_smoke(tmp_path: Path, ecosystem_file: str, marker: str) -> None:
    _write_text(tmp_path / ecosystem_file, marker + "\n")
    (tmp_path / "features").mkdir()
    result = detect(tmp_path)
    assert isinstance(result, Detected)
    assert result.framework.features_dir == Path("features")

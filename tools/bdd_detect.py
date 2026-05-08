"""Detect the project's BDD framework per FORGE design §6.6.

Top-level dependency declarations only — no transitive scan, no lockfile scan.
False positives are worse than missed escalations: when ambiguous (a partial
signal — deps declared but features dir absent, or config malformed), surface
the reason so the calling skill can ask the user once.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

__all__ = (
    "Ambiguous",
    "BDDFramework",
    "Detected",
    "DetectionResult",
    "NotDetected",
    "detect",
)


@dataclass(frozen=True)
class BDDFramework:
    """Resolved BDD framework binding for a project."""

    ecosystem: str
    framework: str
    features_dir: Path


@dataclass(frozen=True)
class Detected:
    """A BDD framework was unambiguously detected."""

    framework: BDDFramework


@dataclass(frozen=True)
class Ambiguous:
    """A partial signal was detected; the calling skill must ask the user once.

    Reasons follow ``"<ecosystem>: <signal-summary>"`` so the skill can surface
    them to the user verbatim and cache the resolution in ``.forge/config.json``.
    """

    reason: str


@dataclass(frozen=True)
class NotDetected:
    """No BDD framework signal of any kind was found."""


DetectionResult = Detected | Ambiguous | NotDetected


_PYTHON_FEATURES_DIR = Path("tests/features")
_NODE_FEATURES_DIR = Path("features")
_RUBY_FEATURES_DIR = Path("features")
_GO_FEATURES_DIR = Path("features")


def _validate_relative_features_dir(raw: str) -> Path:
    """Reject absolute paths and traversal segments in user-supplied features_dir."""
    candidate = Path(raw)
    if candidate.is_absolute():
        raise ValueError("features_dir must be relative")
    if ".." in candidate.parts:
        raise ValueError("features_dir must not contain '..' segments")
    if not candidate.parts or candidate.parts == (".",):
        raise ValueError("features_dir must not be empty")
    return candidate


def _read_forge_config_override(repo_root: Path) -> DetectionResult | None:
    """Return None when no override is configured; otherwise a DetectionResult.

    Malformed or unsafe overrides surface as ``Ambiguous`` so the user fixes
    config rather than silently falling through to auto-detection.
    """
    config_path = repo_root / ".forge" / "config.json"
    if not config_path.is_file():
        return None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return Ambiguous(reason="config: .forge/config.json is not valid JSON")
    bdd = config.get("bdd_framework")
    if not isinstance(bdd, dict):
        return None
    try:
        ecosystem = str(bdd["ecosystem"])
        framework = str(bdd["framework"])
        raw_features_dir = str(bdd["features_dir"])
    except KeyError as exc:
        return Ambiguous(reason=f"config: bdd_framework missing key {exc.args[0]!r}")
    try:
        features_dir = _validate_relative_features_dir(raw_features_dir)
    except ValueError as exc:
        return Ambiguous(reason=f"config: features_dir invalid ({exc})")
    return Detected(
        framework=BDDFramework(
            ecosystem=ecosystem,
            framework=framework,
            features_dir=features_dir,
        ),
    )


def _detect_python(repo_root: Path) -> DetectionResult | None:
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError:
        return None
    project_deps = data.get("project", {}).get("dependencies", [])
    pytest_ini = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
    declared = any("pytest-bdd" in str(dep) for dep in project_deps) or any(
        "pytest-bdd" in str(v) for v in pytest_ini.values()
    )
    if not declared:
        return None
    if not (repo_root / _PYTHON_FEATURES_DIR).is_dir():
        return Ambiguous(
            reason=f"python: pytest-bdd declared but {_PYTHON_FEATURES_DIR}/ missing",
        )
    return Detected(
        framework=BDDFramework(
            ecosystem="python",
            framework="pytest-bdd",
            features_dir=_PYTHON_FEATURES_DIR,
        ),
    )


def _detect_node(repo_root: Path) -> DetectionResult | None:
    pkg = repo_root / "package.json"
    if not pkg.is_file():
        return None
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
    if "@cucumber/cucumber" not in deps:
        return None
    has_config = (repo_root / "cucumber.js").is_file() or (repo_root / "cucumber.cjs").is_file()
    features_dir_present = (repo_root / _NODE_FEATURES_DIR).is_dir()
    missing: list[str] = []
    if not has_config:
        missing.append("cucumber.{js,cjs}")
    if not features_dir_present:
        missing.append(f"{_NODE_FEATURES_DIR}/")
    if missing:
        return Ambiguous(
            reason=f"node: @cucumber/cucumber declared but {' and '.join(missing)} missing",
        )
    return Detected(
        framework=BDDFramework(
            ecosystem="node",
            framework="cucumber-js",
            features_dir=_NODE_FEATURES_DIR,
        ),
    )


def _detect_ruby(repo_root: Path) -> DetectionResult | None:
    gemfile = repo_root / "Gemfile"
    if not gemfile.is_file():
        return None
    if "cucumber" not in gemfile.read_text(encoding="utf-8"):
        return None
    if not (repo_root / _RUBY_FEATURES_DIR).is_dir():
        return Ambiguous(reason="ruby: cucumber gem declared but features/ missing")
    return Detected(
        framework=BDDFramework(
            ecosystem="ruby",
            framework="cucumber-ruby",
            features_dir=_RUBY_FEATURES_DIR,
        ),
    )


def _detect_go(repo_root: Path) -> DetectionResult | None:
    gomod = repo_root / "go.mod"
    if not gomod.is_file():
        return None
    if "github.com/cucumber/godog" not in gomod.read_text(encoding="utf-8"):
        return None
    if not (repo_root / _GO_FEATURES_DIR).is_dir():
        return Ambiguous(reason="go: godog declared but features/ missing")
    return Detected(
        framework=BDDFramework(
            ecosystem="go",
            framework="godog",
            features_dir=_GO_FEATURES_DIR,
        ),
    )


def detect(repo_root: Path) -> DetectionResult:
    """Resolve the project's BDD framework as ``Detected | Ambiguous | NotDetected``.

    Order: forge config override > python > node > ruby > go.

    - ``Detected``: an unambiguous binding to run scenarios against.
    - ``Ambiguous``: a partial signal (e.g., dep declared but features dir
      missing, or config malformed). The calling skill must ask the user once
      and cache the resolution in ``.forge/config.json``.
    - ``NotDetected``: no signal of any kind.

    Args:
        repo_root: Absolute path to the repository root to inspect.

    Returns:
        A ``DetectionResult`` describing the binding, ambiguity, or absence.
    """
    override = _read_forge_config_override(repo_root)
    if override is not None:
        return override
    for detector in (_detect_python, _detect_node, _detect_ruby, _detect_go):
        result = detector(repo_root)
        if result is not None:
            return result
    return NotDetected()

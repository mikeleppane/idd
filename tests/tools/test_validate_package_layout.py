"""Pin the public surface of tools.validate after the P2b package split."""

from __future__ import annotations

import importlib
import inspect
import subprocess
import sys

import pytest

# Truth-set from Step 0.1. Update only via the procedure documented in
# `tools/validate/__init__.py`'s module docstring.
REQUIRED_ALL: frozenset[str] = frozenset(
    {
        "EXIT_NONZERO_SEVERITIES",
        "Finding",
        "Severity",
        "ValidationError",
        "main",
        "validate_anchors",
        "validate_capability_uniqueness",
        "validate_constitution",
        "validate_delta",
        "validate_deviations",
        "validate_frontmatter",
        "validate_health",
        "validate_negative_requirements",
        "validate_plan_tasks",
        "validate_scenarios",
        "validate_verified_deps",
    }
)

# Pinned signatures (keyword-only / positional names). Captured pre-split.
EXPECTED_SIGNATURES: dict[str, tuple[str, ...]] = {
    "validate_constitution": ("path",),
    "validate_delta": ("path",),
    "validate_negative_requirements": ("path",),
    "validate_capability_uniqueness": ("repo_root",),
    "validate_frontmatter": ("path", "kind"),
    "validate_health": ("repo_root",),
    "validate_scenarios": ("path",),
    "validate_anchors": ("path", "repo_root"),
    "validate_plan_tasks": ("plan_path", "spec_path"),
    "validate_deviations": ("feature_root",),
    "validate_verified_deps": ("plan_path", "check_registries"),
}


def test_dunder_all_matches_truth_set() -> None:
    v = importlib.import_module("tools.validate")
    actual = frozenset(v.__all__)
    assert actual == REQUIRED_ALL, (
        f"__all__ drift: extra={actual - REQUIRED_ALL} missing={REQUIRED_ALL - actual}"
    )


def test_public_callables_have_pinned_signatures() -> None:
    v = importlib.import_module("tools.validate")
    for name, expected_params in EXPECTED_SIGNATURES.items():
        sig = inspect.signature(getattr(v, name))
        actual_params = tuple(sig.parameters.keys())
        assert actual_params == expected_params, (
            f"{name} signature drift: expected {expected_params}, got {actual_params}"
        )


@pytest.mark.parametrize(
    "submodule",
    [
        "tools.validate._finding",
        "tools.validate._frontmatter",
        "tools.validate._feature_layout",
        "tools.validate.constitution",
        "tools.validate.delta",
        "tools.validate.plan",
        "tools.validate.spec_semantic",
        "tools.validate.spec_structural",
        "tools.validate.state_semantic",
        "tools.validate.health",
        "tools.validate.cli",
    ],
)
def test_submodules_importable(submodule: str) -> None:
    importlib.import_module(submodule)


def test_python_dash_m_entry_point_runs() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "tools.validate", "--target", "health"],
        check=False,
        capture_output=True,
        text=True,
    )
    # Exit 0 (clean) or 1 (findings) is fine; 2 = usage error must not happen.
    assert proc.returncode in (0, 1), (proc.stdout, proc.stderr)

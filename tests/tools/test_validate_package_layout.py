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
        "GitConventionsConfig",
        "MAX_FIX_HINT_LEN",
        "Severity",
        "ValidationError",
        "main",
        "validate_anchors",
        "validate_capability_spec_sections",
        "validate_capability_uniqueness",
        "validate_config",
        "validate_constitution",
        "validate_conventions",
        "validate_delta",
        "validate_deviations",
        "validate_domain_glossary",
        "validate_frontmatter",
        "validate_git_conventions",
        "validate_health",
        "validate_lessons",
        "validate_negative_requirements",
        "validate_plan_tasks",
        "validate_qa_shape",
        "validate_research",
        "validate_scenarios",
        "validate_tdd_evidence",
        "validate_verified_deps",
    }
)

# Pinned signatures (keyword-only / positional names). Captured pre-split.
EXPECTED_SIGNATURES: dict[str, tuple[str, ...]] = {
    "validate_capability_spec_sections": ("path",),
    "validate_constitution": ("path",),
    "validate_delta": ("path",),
    "validate_negative_requirements": ("path",),
    "validate_capability_uniqueness": ("repo_root",),
    "validate_frontmatter": ("path", "kind"),
    "validate_health": ("repo_root",),
    "validate_lessons": ("repo_root",),
    "validate_scenarios": ("path",),
    "validate_anchors": ("path", "repo_root"),
    "validate_plan_tasks": ("plan_path", "spec_path"),
    "validate_deviations": ("feature_root",),
    "validate_domain_glossary": ("repo_root", "feature_id"),
    "validate_qa_shape": ("repo_root", "feature_id"),
    "validate_tdd_evidence": ("repo_root", "feature_id", "git_show_files"),
    "validate_verified_deps": ("plan_path", "check_registries"),
    "validate_research": ("research_path",),
    "validate_config": ("config_path",),
    "validate_conventions": ("repo_root", "commit_body", "diff"),
    "validate_git_conventions": ("feature_folder", "runner"),
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
        "tools.validate.conventions",
        "tools.validate.delta",
        "tools.validate.domain_glossary",
        "tools.validate.git_conventions",
        "tools.validate.plan",
        "tools.validate.qa_shape",
        "tools.validate.spec_semantic",
        "tools.validate.spec_structural",
        "tools.validate.state_semantic",
        "tools.validate.tdd_evidence",
        "tools.validate.health",
        "tools.validate.lessons",
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

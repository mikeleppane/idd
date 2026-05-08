"""End-to-end smoke regression for ``tools.validate.tdd_evidence``.

Drives the validator against two committed feature-shaped fixtures:

- ``2026-05-08-tdd-paired-feature``: every AC has a paired test->impl
  commit ordered correctly. Validator must return zero findings.
- ``2026-05-08-tdd-missing-feature``: AC-1 has only an impl commit (no
  preceding test), AC-2 has only an impl commit but is excused by a
  ``## TDD Exception: AC-2`` heading in ``decisions.md``. Validator must
  emit exactly one BLOCK finding scoped to AC-1.

The fixture commit SHAs do not exist in real git history, so a fake
``git_show_files`` callable is injected that classifies SHAs by their
recorded subject prefix (``test(...)`` -> tests/, anything else -> src/).
This exercises the diff-suspicion path without subprocess plumbing.
"""

from __future__ import annotations

from pathlib import Path

from tools.validate._finding import Finding
from tools.validate.tdd_evidence import validate_tdd_evidence

SMOKE_ROOT = Path(__file__).resolve().parents[1] / "smoke"

PAIRED_FIXTURE_ID = "2026-05-08-tdd-paired-feature"
MISSING_FIXTURE_ID = "2026-05-08-tdd-missing-feature"


def _fake_git_show_files(sha: str) -> list[str]:
    """Return tests/ paths for any test-prefix SHA, src/ paths otherwise.

    The paired fixture uses SHAs prefixed ``t`` for test commits and ``f``
    for feat commits. Returning a tests/-only path for ``t*`` SHAs keeps
    the suspicious-test-commit advisory silent; returning ``src/`` for
    ``f*`` SHAs is irrelevant because feat commits are not inspected.
    """
    if sha.startswith("t"):
        return ["tests/foo.py"]
    return ["src/foo.py"]


def test_tdd_paired_smoke_passes() -> None:
    """Paired fixture: every AC has test->impl ordering, zero findings expected."""
    repo_root = SMOKE_ROOT / PAIRED_FIXTURE_ID
    findings: list[Finding] = validate_tdd_evidence(
        repo_root,
        PAIRED_FIXTURE_ID,
        git_show_files=_fake_git_show_files,
    )
    assert findings == [], [(f.severity, f.message) for f in findings]


def test_tdd_missing_smoke_blocks() -> None:
    """Missing-pair fixture: AC-1 has impl without test, must surface one BLOCK."""
    repo_root = SMOKE_ROOT / MISSING_FIXTURE_ID
    findings: list[Finding] = validate_tdd_evidence(
        repo_root,
        MISSING_FIXTURE_ID,
        git_show_files=_fake_git_show_files,
    )
    blocks = [f for f in findings if f.severity == "BLOCK"]
    assert len(blocks) == 1, [(f.severity, f.message) for f in findings]
    assert "missing_test_pair" in blocks[0].message
    assert "AC-1" in blocks[0].message


def test_tdd_missing_smoke_excused_ac_silent() -> None:
    """Missing-pair fixture: AC-2 is covered by a TDD Exception ADR; no finding mentions it."""
    repo_root = SMOKE_ROOT / MISSING_FIXTURE_ID
    findings: list[Finding] = validate_tdd_evidence(
        repo_root,
        MISSING_FIXTURE_ID,
        git_show_files=_fake_git_show_files,
    )
    assert all("AC-2" not in f.message for f in findings), [
        (f.severity, f.message) for f in findings
    ]

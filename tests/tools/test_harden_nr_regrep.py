"""Tests for `tools.harden.nr_regrep` — re-greps merged tree against SPEC NRs."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.harden.contract import HardenError
from tools.harden.nr_regrep import (
    NegativeRequirement,
    NRResult,
    NRViolation,
    parse_negative_requirements,
    run_nr_regrep,
)


def _write_spec(repo_root: Path, feature_id: str, body: str) -> Path:
    feature_dir = repo_root / ".forge" / "features" / feature_id
    feature_dir.mkdir(parents=True, exist_ok=True)
    spec = feature_dir / "SPEC.md"
    spec.write_text(body, encoding="utf-8")
    return spec


_NR_EVAL_BODY = "# Negative Requirements\n\n- MUST NOT use `eval` for untrusted input\n"


def test_run_nr_regrep_clean_pass(tmp_path: Path) -> None:
    feature_id = "2026-05-09-nr-clean"
    _write_spec(tmp_path, feature_id, _NR_EVAL_BODY)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "foo.py").write_text(
        "def safe() -> int:\n    return 1\n",
        encoding="utf-8",
    )

    result = run_nr_regrep(tmp_path, feature_id)

    assert isinstance(result, NRResult)
    assert result.status == "pass"
    assert result.nrs_scanned == 1
    assert result.violations == []


def test_run_nr_regrep_violation_returns_fail(tmp_path: Path) -> None:
    feature_id = "2026-05-09-nr-violated"
    _write_spec(tmp_path, feature_id, _NR_EVAL_BODY)
    (tmp_path / "tools").mkdir()
    offender = tmp_path / "tools" / "foo.py"
    offender.write_text(
        "def boom(s: str) -> object:\n    return eval(s)\n",
        encoding="utf-8",
    )

    result = run_nr_regrep(tmp_path, feature_id)

    assert result.status == "fail"
    assert result.nrs_scanned == 1
    assert len(result.violations) == 1
    violation = result.violations[0]
    assert isinstance(violation, NRViolation)
    assert violation.nr_id == "nr-1"
    assert violation.pattern == "eval"
    assert violation.file_path == Path("tools/foo.py")
    assert violation.line_number == 2
    assert "eval" in violation.line_text


def test_run_nr_regrep_no_negative_requirements_returns_pass(tmp_path: Path) -> None:
    feature_id = "2026-05-09-nr-none"
    _write_spec(tmp_path, feature_id, "# Intent\n\nNothing forbidden here.\n")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "foo.py").write_text("eval('1')\n", encoding="utf-8")

    result = run_nr_regrep(tmp_path, feature_id)

    assert result.status == "pass"
    assert result.nrs_scanned == 0
    assert result.violations == []


def test_run_nr_regrep_missing_spec_raises(tmp_path: Path) -> None:
    feature_id = "2026-05-09-nr-no-spec"
    (tmp_path / ".forge" / "features" / feature_id).mkdir(parents=True)

    with pytest.raises(HardenError, match=r"SPEC\.md missing"):
        run_nr_regrep(tmp_path, feature_id)


def test_run_nr_regrep_filter_excludes_tests_by_default(tmp_path: Path) -> None:
    feature_id = "2026-05-09-nr-filter-default"
    _write_spec(tmp_path, feature_id, _NR_EVAL_BODY)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_thing.py").write_text(
        "def test_eval() -> None:\n    eval('1')\n",
        encoding="utf-8",
    )

    result = run_nr_regrep(tmp_path, feature_id)

    assert result.status == "pass"
    assert result.violations == []


def test_run_nr_regrep_custom_filter_overrides_default(tmp_path: Path) -> None:
    feature_id = "2026-05-09-nr-filter-custom"
    _write_spec(tmp_path, feature_id, _NR_EVAL_BODY)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_thing.py").write_text(
        "def test_eval() -> None:\n    eval('1')\n",
        encoding="utf-8",
    )

    def include_everything(_path: Path) -> bool:
        return True

    result = run_nr_regrep(tmp_path, feature_id, file_filter=include_everything)

    assert result.status == "fail"
    assert any(v.file_path == Path("tests/test_thing.py") for v in result.violations)


def test_run_nr_regrep_violations_sorted_deterministically(tmp_path: Path) -> None:
    feature_id = "2026-05-09-nr-sorted"
    _write_spec(tmp_path, feature_id, _NR_EVAL_BODY)
    src = tmp_path / "src"
    src.mkdir()
    # Write files in non-sorted creation order; expect output sorted by path.
    (src / "z_late.py").write_text("eval('z')\n", encoding="utf-8")
    (src / "a_early.py").write_text(
        "x = 1\neval('a')\nz = eval('a2')\n",
        encoding="utf-8",
    )

    result = run_nr_regrep(tmp_path, feature_id)

    assert result.status == "fail"
    paths_lines = [(v.file_path, v.line_number) for v in result.violations]
    assert paths_lines == sorted(paths_lines)
    # Earliest path first, with ascending line numbers within that path.
    assert paths_lines[0] == (Path("src/a_early.py"), 2)
    assert paths_lines[1] == (Path("src/a_early.py"), 3)
    assert paths_lines[2] == (Path("src/z_late.py"), 1)


def test_run_nr_regrep_fence_aware_nr_parse(tmp_path: Path) -> None:
    feature_id = "2026-05-09-nr-fenced"
    body = (
        "# Negative Requirements\n\n"
        "_None for now — example below is illustrative only._\n\n"
        "```markdown\n"
        "- MUST NOT use `eval`\n"
        "```\n"
    )
    _write_spec(tmp_path, feature_id, body)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "foo.py").write_text("eval('1')\n", encoding="utf-8")

    result = run_nr_regrep(tmp_path, feature_id)

    assert result.nrs_scanned == 0
    assert result.status == "pass"
    assert result.violations == []


def test_run_nr_regrep_no_forbidden_patterns_no_violation(tmp_path: Path) -> None:
    feature_id = "2026-05-09-nr-no-pattern"
    body = "# Negative Requirements\n\n- MUST NOT regress.\n"
    _write_spec(tmp_path, feature_id, body)
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "foo.py").write_text(
        "def go() -> int:\n    return 1\n",
        encoding="utf-8",
    )

    result = run_nr_regrep(tmp_path, feature_id)

    assert result.nrs_scanned == 1
    assert result.status == "pass"
    assert result.violations == []


def test_parse_negative_requirements_extracts_backtick_tokens(tmp_path: Path) -> None:
    spec = tmp_path / "SPEC.md"
    spec.write_text(
        "# Negative Requirements\n\n"
        "- MUST NOT use `eval` or `exec` for user input\n"
        "- MUST NOT depend on `os.system`\n",
        encoding="utf-8",
    )

    nrs = parse_negative_requirements(spec)

    assert [nr.nr_id for nr in nrs] == ["nr-1", "nr-2"]
    assert all(isinstance(nr, NegativeRequirement) for nr in nrs)
    assert "eval" in nrs[0].forbidden_patterns
    assert "exec" in nrs[0].forbidden_patterns
    assert "os.system" in nrs[1].forbidden_patterns

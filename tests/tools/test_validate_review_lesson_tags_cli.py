"""CLI dispatch coverage for ``--target review-lesson-tags``.

The in-process validator surface lives in
``tests/tools/test_validate_review_lesson_tags.py``; this module pins the
subprocess entry point so a typo in the CLI registration would surface as a
test failure rather than as a silently missing per-feature gate.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_LESSONS_BODY_HIGH = """---
version: 0.1.0
created: "2026-05-11"
---

# FORGE Lessons

## L001 — example trap
**Captured:** 2026-05-11 from feature 2026-05-11-demo
**Resolved by:** manual
**Trap:** t
**Avoidance:** a
**Tags:** dispatch
**Severity:** HIGH
**Status:** active
"""

_LESSONS_BODY_RETIRED = """---
version: 0.1.0
created: "2026-05-11"
---

# FORGE Lessons

## L001 — retired trap
**Captured:** 2026-05-11 from feature 2026-05-11-demo
**Resolved by:** manual
**Trap:** t
**Avoidance:** a
**Tags:** dispatch
**Severity:** HIGH
**Status:** retired
"""


def _write_lessons(repo_root: Path, body: str) -> Path:
    path = repo_root / ".forge" / "intel" / "lessons.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _write_review(feature: Path, *, rows: list[str]) -> Path:
    feature.mkdir(parents=True, exist_ok=True)
    body = (
        "---\nspec: 2026-05-11-demo\ntarget: code\nstatus: open\ncycles: 1\n---\n\n"
        "# Findings\n\n"
        "| ID | Severity | Status | Resolved by | Location | Problem | Recommended Fix | Source |\n"
        "|----|----------|--------|-------------|----------|---------|-----------------|--------|\n"
        + "\n".join(rows)
        + "\n"
    )
    path = feature / "REVIEW.code.md"
    path.write_text(body, encoding="utf-8")
    return path


def _run_cli(repo_root: Path, feature: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.validate",
            "--target",
            "review-lesson-tags",
            "--repo-root",
            str(repo_root),
            str(feature),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_cli_clean_cross_check_exits_zero(tmp_path: Path) -> None:
    _write_lessons(tmp_path, _LESSONS_BODY_HIGH)
    feature = tmp_path / "feat"
    _write_review(
        feature,
        rows=["| F-1 | HIGH | open | | src/x.py:1 | [lesson:L001] m | f | self |"],
    )
    proc = _run_cli(tmp_path, feature)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    payload = json.loads(proc.stdout)
    assert payload["target"] == "review-lesson-tags"
    assert payload["findings"] == []


def test_cli_unknown_lesson_blocks_nonzero(tmp_path: Path) -> None:
    _write_lessons(tmp_path, _LESSONS_BODY_HIGH)
    feature = tmp_path / "feat"
    _write_review(
        feature,
        rows=["| F-1 | HIGH | open | | src/x.py:1 | [lesson:L999] m | f | self |"],
    )
    proc = _run_cli(tmp_path, feature)
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    payload = json.loads(proc.stdout)
    findings = payload["findings"]
    assert len(findings) == 1
    assert findings[0]["severity"] == "BLOCK"
    assert "unknown lesson 'L999'" in findings[0]["message"]


def test_cli_severity_mismatch_blocks(tmp_path: Path) -> None:
    _write_lessons(tmp_path, _LESSONS_BODY_HIGH)
    feature = tmp_path / "feat"
    _write_review(
        feature,
        rows=["| F-1 | MEDIUM | open | | src/x.py:1 | [lesson:L001] m | f | self |"],
    )
    proc = _run_cli(tmp_path, feature)
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    payload = json.loads(proc.stdout)
    findings = payload["findings"]
    assert len(findings) == 1
    assert findings[0]["severity"] == "BLOCK"
    msg = findings[0]["message"]
    assert "Severity='MEDIUM'" in msg
    assert "lesson L001" in msg
    assert "'HIGH'" in msg


def test_cli_retired_lesson_warns_but_exit_zero(tmp_path: Path) -> None:
    """WARN does not exit non-zero (not in EXIT_NONZERO_SEVERITIES)."""
    _write_lessons(tmp_path, _LESSONS_BODY_RETIRED)
    feature = tmp_path / "feat"
    _write_review(
        feature,
        rows=["| F-1 | HIGH | open | | src/x.py:1 | [lesson:L001] m | f | self |"],
    )
    proc = _run_cli(tmp_path, feature)
    assert proc.returncode == 0, (proc.stdout, proc.stderr)
    payload = json.loads(proc.stdout)
    findings = payload["findings"]
    assert len(findings) == 1
    assert findings[0]["severity"] == "WARN"
    assert "retired" in findings[0]["message"]


def test_cli_missing_path_blocks(tmp_path: Path) -> None:
    """Per-folder targets must surface a BLOCK when the path is omitted."""
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.validate",
            "--target",
            "review-lesson-tags",
            "--repo-root",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    payload = json.loads(proc.stdout)
    findings = payload["findings"]
    assert len(findings) == 1
    assert findings[0]["severity"] == "BLOCK"
    assert "folder path argument" in findings[0]["message"]


def test_cli_nonexistent_folder_blocks(tmp_path: Path) -> None:
    proc = _run_cli(tmp_path, tmp_path / "does-not-exist")
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    payload = json.loads(proc.stdout)
    findings = payload["findings"]
    assert len(findings) == 1
    assert findings[0]["severity"] == "BLOCK"

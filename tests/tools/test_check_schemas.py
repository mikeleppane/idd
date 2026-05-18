"""All shipped JSON Schemas must validate against Draft 2020-12.

Also exercises the `forge-check-schemas` CLI surface: argparse routing, exit
codes, quiet mode, and failure-path messaging.
"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from tools import check_schemas
from tools.check_schemas import main

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = REPO_ROOT / "schemas"

SCHEMA_FILES = sorted(SCHEMA_DIR.glob("*.schema.json"))


@pytest.mark.parametrize("schema_path", SCHEMA_FILES, ids=lambda p: p.name)
def test_schema_is_valid_draft_2020_12(schema_path: Path) -> None:
    payload = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(payload)


def test_main_no_args_exits_zero_and_prints_ok_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main([])
    captured = capsys.readouterr()

    assert rc == 0
    assert "OK schema " in captured.out
    assert "OK template " in captured.out


def test_main_unknown_flag_exits_two(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--bogus"])

    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert "usage:" in captured.err.lower()


def test_main_unknown_positional_exits_two(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["extraneous"])

    assert excinfo.value.code == 2
    captured = capsys.readouterr()
    assert "usage:" in captured.err.lower()


def test_main_help_exits_zero_with_usage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        main(["--help"])

    assert excinfo.value.code == 0
    captured = capsys.readouterr()
    assert "usage:" in captured.out.lower()


def test_main_quiet_suppresses_ok_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(["--quiet"])
    captured = capsys.readouterr()

    assert rc == 0
    assert "OK schema " not in captured.out
    assert "OK template " not in captured.out


def test_main_quiet_short_form_suppresses_ok_lines(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = main(["-q"])
    captured = capsys.readouterr()

    assert rc == 0
    assert "OK schema " not in captured.out
    assert "OK template " not in captured.out


def test_main_passes_when_schema_version_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shipped templates carry schema_version, so the strict gate passes."""
    monkeypatch.setenv("FORGE_SCHEMA_VERSION_REQUIRED", "1")
    assert main(["-q"]) == 0


def test_main_failure_returns_one(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def _raise(path: Path, *, quiet: bool = False) -> None:
        raise jsonschema.SchemaError("synthetic")

    monkeypatch.setattr(check_schemas, "_check_schema", _raise)

    rc = main([])
    captured = capsys.readouterr()

    assert rc == 1
    assert "FAIL:" in captured.err

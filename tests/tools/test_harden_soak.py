"""Tests for `tools.harden.soak` — long-running soak check with entrypoint detection."""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.harden.contract import HardenError
from tools.harden.soak import (
    DEFAULT_MEMORY_GROWTH_THRESHOLD,
    DEFAULT_SOAK_MINUTES,
    EntrypointInfo,
    SoakResult,
    SoakSample,
    detect_entrypoint,
    run_soak,
)


def _write_spec(repo_root: Path, feature_id: str) -> Path:
    feature_dir = repo_root / ".forge" / "features" / feature_id
    feature_dir.mkdir(parents=True, exist_ok=True)
    target = feature_dir / "SPEC.md"
    target.write_text("# Spec\n\nstub spec\n", encoding="utf-8")
    return target


def _stable_samples(count: int = 5, rss: int = 100 * 1024 * 1024) -> list[SoakSample]:
    return [
        SoakSample(
            sample_index=index,
            elapsed_seconds=float(index) * 30.0,
            rss_bytes=rss,
            cpu_percent=2.0,
        )
        for index in range(count)
    ]


def test_default_constants_match_spec() -> None:
    assert DEFAULT_SOAK_MINUTES == 5
    assert DEFAULT_MEMORY_GROWTH_THRESHOLD == 0.10


def test_detect_entrypoint_python_script_from_pyproject(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        "[project]\n"
        'name = "demo"\n'
        'version = "0.1.0"\n'
        "\n"
        "[project.scripts]\n"
        'foo = "demo.cli:main"\n'
        'bar = "demo.cli:other"\n',
        encoding="utf-8",
    )

    info = detect_entrypoint(tmp_path)

    assert info.kind == "python_script"
    assert info.name == "foo"
    assert info.command == "foo"


def test_detect_entrypoint_node_bin_object_from_package_json(tmp_path: Path) -> None:
    package = tmp_path / "package.json"
    package.write_text(
        '{"name": "demo", "version": "1.0.0", "bin": {"bar": "./cli.js"}}\n',
        encoding="utf-8",
    )

    info = detect_entrypoint(tmp_path)

    assert info.kind == "node_bin"
    assert info.name == "bar"
    assert info.command == "bar"


def test_detect_entrypoint_node_bin_string_from_package_json(tmp_path: Path) -> None:
    package = tmp_path / "package.json"
    package.write_text(
        '{"name": "demo-cli", "version": "1.0.0", "bin": "./cli.js"}\n',
        encoding="utf-8",
    )

    info = detect_entrypoint(tmp_path)

    assert info.kind == "node_bin"
    # Falls back to package name when bin is a bare string.
    assert info.name == "demo-cli"


def test_detect_entrypoint_none_for_library(tmp_path: Path) -> None:
    info = detect_entrypoint(tmp_path)

    assert info.kind == "none"
    assert info.name is None
    assert info.command is None


def test_detect_entrypoint_pyproject_without_scripts_falls_through(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )

    info = detect_entrypoint(tmp_path)

    assert info.kind == "none"


def test_run_soak_skipped_for_library(tmp_path: Path) -> None:
    feature_id = "2026-05-09-soak-library"
    _write_spec(tmp_path, feature_id)

    def runner(_info: EntrypointInfo, _minutes: int) -> list[SoakSample]:
        raise AssertionError("runner must not be invoked when no entrypoint is detected")

    result = run_soak(tmp_path, feature_id, runner=runner)

    assert isinstance(result, SoakResult)
    assert result.status == "skipped"
    assert result.entrypoint.kind == "none"
    assert result.samples == []
    assert result.duration_seconds == 0.0
    assert result.rss_peak_bytes == 0
    assert result.cpu_peak_percent == 0.0
    assert result.restart_count == 0
    assert result.monotonic_growth_flagged is False
    assert "no long-running entrypoint" in result.detail


def test_run_soak_pass_with_stable_memory(tmp_path: Path) -> None:
    feature_id = "2026-05-09-soak-stable"
    _write_spec(tmp_path, feature_id)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
        '[project.scripts]\ndemo = "demo.cli:main"\n',
        encoding="utf-8",
    )

    samples = _stable_samples(count=5, rss=100 * 1024 * 1024)

    def runner(info: EntrypointInfo, _minutes: int) -> list[SoakSample]:
        assert info.kind == "python_script"
        return samples

    result = run_soak(tmp_path, feature_id, runner=runner)

    assert result.status == "pass"
    assert result.entrypoint.kind == "python_script"
    assert result.samples == samples
    assert result.rss_peak_bytes == 100 * 1024 * 1024
    assert result.cpu_peak_percent == 2.0
    assert result.restart_count == 0
    assert result.monotonic_growth_flagged is False
    assert result.duration_seconds == samples[-1].elapsed_seconds


def test_run_soak_fail_on_memory_growth(tmp_path: Path) -> None:
    feature_id = "2026-05-09-soak-growth"
    _write_spec(tmp_path, feature_id)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
        '[project.scripts]\ndemo = "demo.cli:main"\n',
        encoding="utf-8",
    )

    base_rss = 100 * 1024 * 1024
    grown_samples = [
        SoakSample(sample_index=0, elapsed_seconds=0.0, rss_bytes=base_rss, cpu_percent=1.0),
        SoakSample(
            sample_index=1, elapsed_seconds=30.0, rss_bytes=int(base_rss * 1.2), cpu_percent=1.5
        ),
        SoakSample(
            sample_index=2, elapsed_seconds=60.0, rss_bytes=int(base_rss * 1.5), cpu_percent=2.0
        ),
    ]

    def runner(_info: EntrypointInfo, _minutes: int) -> list[SoakSample]:
        return grown_samples

    result = run_soak(tmp_path, feature_id, runner=runner)

    assert result.status == "fail"
    assert result.monotonic_growth_flagged is True
    assert result.rss_peak_bytes == int(base_rss * 1.5)


def test_run_soak_fail_on_restart(tmp_path: Path) -> None:
    feature_id = "2026-05-09-soak-restart"
    _write_spec(tmp_path, feature_id)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
        '[project.scripts]\ndemo = "demo.cli:main"\n',
        encoding="utf-8",
    )

    samples = _stable_samples()

    def runner(_info: EntrypointInfo, _minutes: int) -> list[SoakSample]:
        return samples

    def restart_observer(_info: EntrypointInfo) -> int:
        return 1

    result = run_soak(
        tmp_path,
        feature_id,
        runner=runner,
        restart_observer=restart_observer,
    )

    assert result.status == "fail"
    assert result.restart_count == 1
    assert result.monotonic_growth_flagged is False


def test_run_soak_default_runner_skips(tmp_path: Path) -> None:
    feature_id = "2026-05-09-soak-default-runner"
    _write_spec(tmp_path, feature_id)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
        '[project.scripts]\ndemo = "demo.cli:main"\n',
        encoding="utf-8",
    )

    result = run_soak(tmp_path, feature_id)

    assert result.status == "skipped"
    assert result.entrypoint.kind == "python_script"
    assert result.samples == []
    assert "no soak runner configured" in result.detail


def test_run_soak_missing_spec_raises(tmp_path: Path) -> None:
    feature_id = "2026-05-09-soak-no-spec"

    with pytest.raises(HardenError, match=r"SPEC\.md missing"):
        run_soak(tmp_path, feature_id)


def test_run_soak_growth_threshold_configurable(tmp_path: Path) -> None:
    feature_id = "2026-05-09-soak-threshold"
    _write_spec(tmp_path, feature_id)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
        '[project.scripts]\ndemo = "demo.cli:main"\n',
        encoding="utf-8",
    )

    base_rss = 100 * 1024 * 1024
    samples = [
        SoakSample(sample_index=0, elapsed_seconds=0.0, rss_bytes=base_rss, cpu_percent=1.0),
        SoakSample(
            sample_index=1, elapsed_seconds=30.0, rss_bytes=int(base_rss * 1.3), cpu_percent=1.5
        ),
    ]

    def runner(_info: EntrypointInfo, _minutes: int) -> list[SoakSample]:
        return samples

    result = run_soak(
        tmp_path,
        feature_id,
        runner=runner,
        growth_threshold=0.5,
    )

    assert result.status == "pass"
    assert result.monotonic_growth_flagged is False
    assert result.rss_peak_bytes == int(base_rss * 1.3)

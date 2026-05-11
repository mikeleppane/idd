"""Tests for tools.constitution_amend.collect_bootstrap_signals."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any, cast

import pytest

from tools.constitution_amend import (
    _PER_FILE_CAP_BYTES,
    AmendError,
    BootstrapSignals,
    SignalFile,
    _read_and_truncate,
    collect_bootstrap_signals,
)


def _write(repo: Path, rel: str, body: str | bytes) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(body, bytes):
        target.write_bytes(body)
    else:
        target.write_text(body, encoding="utf-8")


def test_collect_bootstrap_signals_picks_up_pyproject_and_readme(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", '[project]\nname = "demo"\n')
    _write(tmp_path, "README.md", "# Demo\n\nA small project.\n")

    result = collect_bootstrap_signals(tmp_path)

    assert isinstance(result, BootstrapSignals)
    rels = [f.relative_path for f in result.files]
    assert rels == [PurePosixPath("pyproject.toml"), PurePosixPath("README.md")]
    assert all(isinstance(f, SignalFile) for f in result.files)
    assert all(not f.truncated for f in result.files)
    assert result.dropped_for_secrets == []
    assert result.truncated == []
    expected_total = sum(len(f.body.encode("utf-8")) for f in result.files)
    assert result.total_bytes == expected_total


def test_collect_bootstrap_signals_priority_order_across_ecosystems(tmp_path: Path) -> None:
    _write(tmp_path, "Cargo.toml", '[package]\nname = "x"\n')
    _write(tmp_path, "go.mod", "module example.com/x\n")
    _write(tmp_path, "AGENTS.md", "# Agents\n")
    _write(tmp_path, "README.md", "# Readme\n")
    _write(tmp_path, "package.json", '{"name":"x"}\n')

    result = collect_bootstrap_signals(tmp_path)
    rels = [f.relative_path for f in result.files]

    # Manifest priority: pyproject (absent) > package.json > Cargo.toml > go.mod ...
    # Doc priority: AGENTS.md > CLAUDE.md (absent) > README.md
    assert rels == [
        PurePosixPath("package.json"),
        PurePosixPath("Cargo.toml"),
        PurePosixPath("go.mod"),
        PurePosixPath("AGENTS.md"),
        PurePosixPath("README.md"),
    ]


def test_collect_bootstrap_signals_caps_file_count_at_eight(tmp_path: Path) -> None:
    manifests = [
        "pyproject.toml",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "Gemfile",
        "pom.xml",
        "build.gradle",
        "mix.exs",
        "composer.json",
        "thing.csproj",
        "AGENTS.md",
        "README.md",
    ]
    for name in manifests:
        _write(tmp_path, name, f"# {name}\n")

    result = collect_bootstrap_signals(tmp_path)

    assert len(result.files) == 8
    rels = [f.relative_path for f in result.files]
    # First 8 in priority order: 9 manifests above + csproj fill the 8-slot cap.
    assert rels == [
        PurePosixPath("pyproject.toml"),
        PurePosixPath("package.json"),
        PurePosixPath("Cargo.toml"),
        PurePosixPath("go.mod"),
        PurePosixPath("Gemfile"),
        PurePosixPath("pom.xml"),
        PurePosixPath("build.gradle"),
        PurePosixPath("mix.exs"),
    ]


def test_collect_bootstrap_signals_truncates_oversized_file(tmp_path: Path) -> None:
    big_body = "a" * (20 * 1024)  # 20 KiB
    _write(tmp_path, "README.md", big_body)

    result = collect_bootstrap_signals(tmp_path)

    assert len(result.files) == 1
    sf = result.files[0]
    assert sf.truncated is True
    assert sf.relative_path in result.truncated
    marker = "\n--- truncated at 16384 bytes ---\n"
    assert sf.body.endswith(marker)
    # Body content is exactly first 16384 bytes (decoded) + marker.
    body_bytes = sf.body.encode("utf-8")
    assert len(body_bytes) == 16384 + len(marker.encode("utf-8"))


def test_collect_bootstrap_signals_does_not_truncate_at_exactly_cap(tmp_path: Path) -> None:
    body = "z" * 16384
    _write(tmp_path, "README.md", body)

    result = collect_bootstrap_signals(tmp_path)

    assert len(result.files) == 1
    assert result.files[0].truncated is False
    assert result.truncated == []
    assert result.files[0].body == body


def test_collect_bootstrap_signals_total_payload_cap_stops_growth(tmp_path: Path) -> None:
    # Six manifests at ~15 KiB each — cumulative would exceed 80 KiB.
    body = "x" * (15 * 1024)
    names = ["pyproject.toml", "package.json", "Cargo.toml", "go.mod", "Gemfile", "pom.xml"]
    for name in names:
        _write(tmp_path, name, body)

    result = collect_bootstrap_signals(tmp_path)

    assert result.total_bytes <= 81920
    # The fifth file (5 * 15 KiB = 76 800 < 81920) fits, the sixth (~92 KiB) would not.
    assert len(result.files) == 5


def test_collect_bootstrap_signals_drops_dotenv_via_deny_glob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force a deny glob that matches a real candidate so the path-deny branch
    # is exercised. The default deny globs target files that aren't in the
    # candidate priority list today, so this monkeypatch is the only way to
    # cover the branch without injecting non-candidate names.
    monkeypatch.setattr("tools.constitution_amend._DENY_GLOBS", (".env*", "*.pem", "pyproject.*"))
    _write(tmp_path, "pyproject.toml", '[project]\nname = "x"\n')
    _write(tmp_path, "README.md", "# Demo\n")

    result = collect_bootstrap_signals(tmp_path)

    rels = [f.relative_path for f in result.files]
    assert PurePosixPath("pyproject.toml") not in rels
    assert PurePosixPath("pyproject.toml") in result.dropped_for_secrets
    assert PurePosixPath("README.md") in rels


def test_collect_bootstrap_signals_drops_secret_in_readme(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", '[project]\nname = "x"\n')
    _write(
        tmp_path,
        "README.md",
        '# Demo\n\nConfig:\napi_key = "sk-live-1234567890abcdef"\n',
    )

    result = collect_bootstrap_signals(tmp_path)

    rels = [f.relative_path for f in result.files]
    assert PurePosixPath("README.md") not in rels
    assert PurePosixPath("README.md") in result.dropped_for_secrets


def test_collect_bootstrap_signals_is_deterministic(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", '[project]\nname = "x"\n')
    _write(tmp_path, "package.json", '{"name":"x"}\n')
    _write(tmp_path, "AGENTS.md", "# Agents\n")
    _write(tmp_path, "README.md", "# Readme\n")

    first = collect_bootstrap_signals(tmp_path)
    second = collect_bootstrap_signals(tmp_path)

    assert first == second


def test_collect_bootstrap_signals_picks_first_csproj_by_sorted_name(tmp_path: Path) -> None:
    _write(tmp_path, "Zeta.csproj", "<Project Sdk='Microsoft.NET.Sdk' />\n")
    _write(tmp_path, "Alpha.csproj", "<Project Sdk='Microsoft.NET.Sdk' />\n")

    result = collect_bootstrap_signals(tmp_path)

    rels = [f.relative_path for f in result.files]
    assert PurePosixPath("Alpha.csproj") in rels
    assert PurePosixPath("Zeta.csproj") not in rels


def test_collect_bootstrap_signals_csproj_glob_is_non_recursive(tmp_path: Path) -> None:
    _write(tmp_path, "src/Nested.csproj", "<Project />\n")

    result = collect_bootstrap_signals(tmp_path)

    rels = [f.relative_path for f in result.files]
    assert PurePosixPath("src/Nested.csproj") not in rels
    assert not any(".csproj" in str(r) for r in rels)


def test_collect_bootstrap_signals_raises_when_repo_root_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    with pytest.raises((FileNotFoundError, AmendError)):
        collect_bootstrap_signals(missing)


def test_collect_bootstrap_signals_handles_non_utf8_bytes(tmp_path: Path) -> None:
    # Invalid UTF-8 sequence; decode with errors="replace".
    _write(tmp_path, "README.md", b"# Demo\n\xff\xfeinvalid\n")

    result = collect_bootstrap_signals(tmp_path)

    assert len(result.files) == 1
    assert "�" in result.files[0].body or "Demo" in result.files[0].body


def test_collect_bootstrap_signals_dropped_secret_does_not_count_to_total(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", '[project]\nname = "x"\n')
    _write(
        tmp_path,
        "README.md",
        'password = "hunter2supersecretvalue123"\n',
    )

    result = collect_bootstrap_signals(tmp_path)

    assert PurePosixPath("README.md") in result.dropped_for_secrets
    expected_total = sum(len(f.body.encode("utf-8")) for f in result.files)
    assert result.total_bytes == expected_total


def test_read_and_truncate_reads_at_most_cap_plus_one_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: the helper must bound its binary read to
    ``_PER_FILE_CAP_BYTES + 1`` bytes — never the whole file.

    Guards against a regression to ``path.read_bytes()`` which would load
    a multi-GB README into memory before applying the 16 KiB truncation.
    Spies ``Path.open`` for the candidate and records every ``read(n)``
    call; asserts the only request size is the bounded sentinel.
    """
    huge = tmp_path / "README.md"
    # 4x cap — enough to make an "unbounded read" pull noticeable bytes.
    huge.write_bytes(b"x" * (_PER_FILE_CAP_BYTES * 4))

    real_open = Path.open
    sizes: list[int] = []

    class _Spy:
        def __init__(self, fh: Any) -> None:
            self._fh = fh

        def __enter__(self) -> _Spy:
            self._fh.__enter__()
            return self

        def __exit__(self, *exc: Any) -> None:
            self._fh.__exit__(*exc)

        def read(self, n: int = -1) -> bytes:
            sizes.append(n)
            return cast(bytes, self._fh.read(n))

    def patched(self: Path, *a: Any, **kw: Any) -> Any:
        fh = real_open(self, *a, **kw)
        if self == huge:
            return _Spy(fh)
        return fh

    monkeypatch.setattr(Path, "open", patched)

    body, truncated = _read_and_truncate(huge)

    assert truncated is True
    assert sizes == [_PER_FILE_CAP_BYTES + 1], (
        f"expected exactly one read of {_PER_FILE_CAP_BYTES + 1} bytes; got {sizes}"
    )
    # Body is capped + marker; never reaches the 4x cap size of the source.
    body_bytes = body.encode("utf-8")
    assert len(body_bytes) < _PER_FILE_CAP_BYTES * 2

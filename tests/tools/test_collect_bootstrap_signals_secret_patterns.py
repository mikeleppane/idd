"""Regression tests for the expanded secret-detection scanner.

Pins the audit motivations for the hardened secret-content scanner used by
:func:`tools.constitution_amend.collect_bootstrap_signals`:

* The original ``_SECRET_CONTENT_RE`` matched a narrow label-shape
  (``api_key``/``secret``/``password``/``token`` plus a 12+ char value)
  and produced two correctness problems:

  - False-positives on legitimate prose that mentioned a keyword next to
    a function call (``secret = compute_hash(input_string)``) or English
    copy (``password: NewPassword123Required``). The README would silently
    drop from the bootstrap payload, robbing the drafting LLM of context.
  - False-negatives for every modern token shape that actually leaks in
    real repos: AWS access keys, GitHub PATs, JWTs, Stripe live keys,
    Slack tokens, GCP service-account JSON, PEM private-key markers.

* The new scanner enumerates explicit token shapes, requires quoted
  delimiters on the generic-assignment fallback (20+ chars), and surfaces
  the matched pattern label so callers can tell the user WHICH shape
  fired the drop rather than the legacy generic "secret-shaped content".

* The structured ``dropped_records`` field carries reason-tagged entries
  (``deny_glob`` vs ``secret_content``) with a ``detail`` string naming
  the matching glob or pattern label. The legacy ``dropped`` and
  ``dropped_for_secrets`` collections stay populated identically so
  existing call sites keep working.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from tools.constitution_amend import (
    _DENY_GLOBS,
    _SECRET_PATTERNS,
    BootstrapSignals,
    DroppedFile,
    _detect_secret,
    _name_matches_deny_glob,
    collect_bootstrap_signals,
)


def _write(repo: Path, rel: str, body: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")


# --- Deny-glob expansion ----------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        # private-key / cert file shapes
        "config.ppk",
        "client.p12",
        "client.pfx",
        "vault.kdbx",
        "server.crt",
        "server.cer",
        "truststore.jks",
        "store.keystore",
        # credential JSON / config file shapes
        "credentials.json",
        "service-account-prod.json",
        ".npmrc",
        ".netrc",
        ".git-credentials",
        "kubeconfig",
    ],
)
def test_expanded_deny_globs_match_credential_filenames(name: str) -> None:
    """Each new deny-glob fires on its canonical filename shape."""
    assert _name_matches_deny_glob(name), (
        f"expected {name!r} to match a deny-glob entry in {_DENY_GLOBS}"
    )


@pytest.mark.parametrize(
    "name",
    [
        # prose-named docs that mention credentials conceptually
        "README-credentials-howto.md",
        "not-kubeconfig.txt",
        "kdbx-recovery-guide.md",
        "netrc-explainer.md",
        # near-miss: looks similar but missing the literal hyphen separator
        "service-accountprod.json",
        # near-miss: explicit example file
        "not-credentials.json.example",
    ],
)
def test_expanded_deny_globs_do_not_match_prose_documents(name: str) -> None:
    """Prose docs that merely mention credentials must not trip the deny-glob."""
    assert not _name_matches_deny_glob(name), (
        f"{name!r} should NOT match any deny-glob in {_DENY_GLOBS}"
    )


def test_kubeconfig_matches_as_bare_filename() -> None:
    """``kubeconfig`` carries no extension — must still match by literal name."""
    assert _name_matches_deny_glob("kubeconfig")
    assert not _name_matches_deny_glob("mykubeconfig")
    assert not _name_matches_deny_glob("kubeconfig.bak")


# --- Secret-pattern positives (modern token shapes the old regex missed) ----


@pytest.mark.parametrize(
    ("body", "label"),
    [
        # AWS access key id — 16 uppercase alnum after AKIA prefix.
        ("Sample creds:\nAKIAIOSFODNN7EXAMPLE\n", "aws_access_key"),
        # GitHub personal access token — gh{p,o,u,s,r}_ + 36+ chars.
        ("token in CI: ghp_" + "x" * 36 + "\n", "github_pat"),
        ("oauth: gho_" + "y" * 36 + "\n", "github_pat"),
        # Stripe live key — sk_live_ + 24+ chars.
        ("STRIPE_KEY=sk_live_" + "4" * 28, "stripe_live_key"),
        # Slack bot / user / app token — xox{a,b,p,r,s}- prefix.
        ("slack: xoxb-123456789012-abcdefghij\n", "slack_token"),
        ("slack: xoxp-1234567890-abcdefghij\n", "slack_token"),
        # JWT — three base64url segments joined by dots.
        ("token: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature\n", "jwt"),
        # PEM private-key markers (covering several BEGIN variants).
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n", "pem_private_key"),
        ("-----BEGIN OPENSSH PRIVATE KEY-----\nb3Bl...\n", "pem_private_key"),
        ("-----BEGIN PRIVATE KEY-----\nMIIE...\n", "pem_private_key"),
        # GCP service account JSON marker.
        ('{\n  "type": "service_account",\n  "project_id": "x"\n}', "gcp_service_account"),
        # Quoted generic-assignment fallback — covers legacy-shape leaks.
        ('config: api_key = "abcdef1234567890ABCDEF"\n', "generic_assignment"),
        ('settings: client_secret: "ZZZZZZZZZZZZZZZZZZZZZZZZ"\n', "generic_assignment"),
        ('config: bearer_token: "abcdefghij1234567890XYZ"\n', "generic_assignment"),
    ],
)
def test_detect_secret_returns_label_for_each_known_shape(body: str, label: str) -> None:
    """Each known credential shape returns the expected pattern label."""
    detected = _detect_secret(body)
    assert detected == label, f"expected label {label!r} for body, got {detected!r}"


def test_detect_secret_returns_none_when_no_pattern_fires() -> None:
    """Plain prose without any credential-shaped token returns None."""
    body = "# Demo\n\nA tiny project that does NOT leak anything sensitive.\n"
    assert _detect_secret(body) is None


# --- Secret-pattern negatives (false-positive prevention) -------------------


@pytest.mark.parametrize(
    "body",
    [
        # function call right-hand-side — old regex flagged via 'secret' prefix.
        "secret = compute_hash(input_string)\n",
        # English prose without quote delimiters — old regex flagged.
        "Note: password: NewPassword123Required will be rejected by the validator.\n",
        # code reference — function-call value, not a literal credential.
        "token: getAuthToken()\n",
        # docstring parameter reference — keyword far from any value.
        ":param api_key: the user's API key for authentication.\n",
        # prose discussing tokens conceptually
        "The function compute_secret_hash() returns a stable SHA-256 digest.\n",
        # README explaining how authentication tokens work in general
        "Users acquire a token after signing in and present it on each request.\n",
        # generic assignment but value too short (< 20 chars) — not a real secret.
        'api_key = "shortvalue"\n',
        # generic assignment but unquoted — drops the false-positive class.
        "api_key = foo_bar_baz_qux_quux_corge\n",
    ],
)
def test_detect_secret_ignores_legitimate_prose_and_code(body: str) -> None:
    """False-positive prevention: legitimate prose and code stay unflagged."""
    assert _detect_secret(body) is None, f"unexpected positive match for legitimate body: {body!r}"


# --- Pattern ordering -------------------------------------------------------


def test_specific_patterns_listed_before_generic_assignment() -> None:
    """``generic_assignment`` is the broadest pattern and MUST be ordered last.

    Without this ordering, an AWS key embedded as ``access_key = "AKIA..."``
    would report the generic label instead of the more informative
    ``aws_access_key`` shape. Pin the order so future edits don't regress
    the user-visible label.
    """
    labels = [pattern.label for pattern in _SECRET_PATTERNS]
    assert labels[-1] == "generic_assignment", (
        f"generic_assignment must be last; got order {labels}"
    )


# --- Drop-reason structured collection --------------------------------------


def test_dropped_records_carry_deny_glob_reason_and_detail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file dropped via deny-glob surfaces a structured record naming the glob."""
    # Monkeypatch _DENY_GLOBS so a real candidate (pyproject.toml) trips a
    # deny-glob entry — the default deny set never matches a candidate name,
    # so this is the only way to exercise the deny-glob branch.
    monkeypatch.setattr("tools.constitution_amend._DENY_GLOBS", ("pyproject.*",))
    _write(tmp_path, "pyproject.toml", '[project]\nname = "x"\n')
    _write(tmp_path, "README.md", "# Demo\n")

    result = collect_bootstrap_signals(tmp_path)

    records = [r for r in result.dropped_records if r.relative_path.name == "pyproject.toml"]
    assert len(records) == 1
    record = records[0]
    assert record.reason == "deny_glob"
    assert record.detail == "pyproject.*"


def test_dropped_records_carry_secret_content_reason_and_label(tmp_path: Path) -> None:
    """A file dropped via secret-content surfaces a record naming the pattern label."""
    _write(tmp_path, "pyproject.toml", '[project]\nname = "x"\n')
    _write(
        tmp_path,
        "README.md",
        '# Demo\n\nConfig:\napi_key = "abcdef1234567890ABCDEF"\n',
    )

    result = collect_bootstrap_signals(tmp_path)

    records = [r for r in result.dropped_records if r.relative_path.name == "README.md"]
    assert len(records) == 1
    record = records[0]
    assert record.reason == "secret_content"
    assert record.detail == "generic_assignment"


def test_dropped_records_use_dataclass_shape(tmp_path: Path) -> None:
    """Every entry in ``dropped_records`` is a :class:`DroppedFile`."""
    _write(
        tmp_path,
        "README.md",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n",
    )

    result = collect_bootstrap_signals(tmp_path)

    assert all(isinstance(r, DroppedFile) for r in result.dropped_records)
    record = next(r for r in result.dropped_records if r.relative_path.name == "README.md")
    assert record.reason == "secret_content"
    assert record.detail == "pem_private_key"


def test_legacy_dropped_for_secrets_includes_both_deny_glob_and_secret_drops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The back-compat ``dropped_for_secrets`` union covers both drop reasons."""
    monkeypatch.setattr("tools.constitution_amend._DENY_GLOBS", ("pyproject.*",))
    _write(tmp_path, "pyproject.toml", '[project]\nname = "x"\n')
    _write(
        tmp_path,
        "README.md",
        'aws_creds = "AKIAIOSFODNN7EXAMPLE_FILLER"\n',
    )

    result = collect_bootstrap_signals(tmp_path)

    assert PurePosixPath("pyproject.toml") in result.dropped_for_secrets
    assert PurePosixPath("README.md") in result.dropped_for_secrets
    # Slice-2 escape collection stays disjoint — neither drop is a symlink escape.
    assert result.dropped_for_escape == []


def test_dropped_records_skip_symlink_escape_path(tmp_path: Path) -> None:
    """Symlink-escape drops land in ``dropped_for_escape``, NOT in ``dropped_records``.

    Slice 2 owns the escape collection; slice 3 must not silently fold escape
    drops into the new structured records list.
    """
    outside_root = tmp_path.parent / f"{tmp_path.name}-outside"
    outside_root.mkdir()
    outside = outside_root / "secret.txt"
    outside.write_text("OUT_OF_TREE\n", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").symlink_to(outside)

    result = collect_bootstrap_signals(repo)

    assert PurePosixPath("README.md") in result.dropped_for_escape
    assert PurePosixPath("README.md") not in [r.relative_path for r in result.dropped_records]


# --- BootstrapSignals dataclass surface -------------------------------------


def test_bootstrap_signals_exposes_dropped_records_field(tmp_path: Path) -> None:
    """``BootstrapSignals`` carries a ``dropped_records`` list on every call."""
    _write(tmp_path, "README.md", "# clean readme\n")

    result = collect_bootstrap_signals(tmp_path)

    assert isinstance(result, BootstrapSignals)
    assert isinstance(result.dropped_records, list)
    # No drops on a clean tree.
    assert result.dropped_records == []

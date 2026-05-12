"""Tests for the cross_ai config loader.

Covers default-return paths (missing file, missing block), schema validation
delegation to Draft 2020-12, ReDoS guard rejection (length cap + probe-compile),
and dataclass immutability of the returned CrossAiConfig.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from tools import redaction
from tools.cross_ai.config import (
    REDACTION_REGEX_MAX_LEN,
    CrossAiConfig,
    CrossAiConfigError,
    CrossAiMode,
    RedactionRules,
    RetryPolicy,
    load_config,
    to_redaction_config,
)


def _write_config(repo_root: Path, payload: dict[str, object]) -> None:
    """Write ``payload`` as ``.forge/config.json`` under ``repo_root``."""
    forge_dir = repo_root / ".forge"
    forge_dir.mkdir(parents=True, exist_ok=True)
    (forge_dir / "config.json").write_text(json.dumps(payload), encoding="utf-8")


def test_missing_config_file_returns_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    assert cfg == CrossAiConfig()
    assert cfg.mode is CrossAiMode.manual
    assert cfg.allowed_clis == ()
    assert cfg.timeout_seconds == 120
    assert cfg.max_prompt_tokens == 100_000
    assert cfg.cost_warn_threshold_usd == 0.50
    assert cfg.retry == RetryPolicy()
    assert cfg.redaction == RedactionRules()
    assert cfg.dispatch_approved_at is None
    assert cfg.dispatch_approved_by is None


def test_missing_cross_ai_block_returns_defaults(tmp_path: Path) -> None:
    _write_config(tmp_path, {"unrelated": {"foo": "bar"}})
    cfg = load_config(tmp_path)
    assert cfg == CrossAiConfig()


def test_manual_mode_only_populates_mode(tmp_path: Path) -> None:
    _write_config(tmp_path, {"cross_ai": {"mode": "manual"}})
    cfg = load_config(tmp_path)
    assert cfg.mode is CrossAiMode.manual
    # All other fields keep their defaults.
    assert cfg.allowed_clis == ()
    assert cfg.timeout_seconds == 120


def test_auto_mode_with_allowed_clis_fully_populated(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        {
            "cross_ai": {
                "mode": "auto",
                "allowed_clis": ["codex"],
                "timeout_seconds": 200,
                "max_prompt_tokens": 50_000,
                "cost_warn_threshold_usd": 1.25,
                "retry": {"max": 2, "backoff_seconds": 15},
                "redaction": {
                    "deny_globs": ["**/*.pem"],
                    "allow_globs": ["docs/**"],
                },
                "dispatch_approved_at": "2026-05-10T00:00:00Z",
                "dispatch_approved_by": "operator@example.com",
            }
        },
    )
    cfg = load_config(tmp_path)
    assert cfg.mode is CrossAiMode.auto
    assert cfg.allowed_clis == ("codex",)
    assert cfg.timeout_seconds == 200
    assert cfg.max_prompt_tokens == 50_000
    assert cfg.cost_warn_threshold_usd == 1.25
    assert cfg.retry == RetryPolicy(max=2, backoff_seconds=15)
    assert cfg.redaction.deny_globs == ("**/*.pem",)
    assert cfg.redaction.allow_globs == ("docs/**",)
    assert cfg.redaction.deny_regex == ()
    assert cfg.redaction.fatal_regex == ()
    assert cfg.dispatch_approved_at == "2026-05-10T00:00:00Z"
    assert cfg.dispatch_approved_by == "operator@example.com"


def test_auto_mode_without_allowed_clis_rejected(tmp_path: Path) -> None:
    _write_config(tmp_path, {"cross_ai": {"mode": "auto"}})
    with pytest.raises(CrossAiConfigError):
        load_config(tmp_path)


def test_redos_guard_rejects_overlong_deny_regex(tmp_path: Path) -> None:
    overlong = "a" * (REDACTION_REGEX_MAX_LEN + 44)
    _write_config(
        tmp_path,
        {"cross_ai": {"mode": "manual", "redaction": {"deny_regex": [overlong]}}},
    )
    with pytest.raises(CrossAiConfigError, match="256-char"):
        load_config(tmp_path)


def test_invalid_deny_regex_rejected(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        {"cross_ai": {"mode": "manual", "redaction": {"deny_regex": ["[unclosed"]}}},
    )
    with pytest.raises(CrossAiConfigError, match="invalid regex"):
        load_config(tmp_path)


def test_fatal_regex_accepted_and_populated(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        {
            "cross_ai": {
                "mode": "manual",
                "redaction": {"fatal_regex": ["sk-[A-Za-z0-9]{32}"]},
            }
        },
    )
    cfg = load_config(tmp_path)
    assert cfg.redaction.fatal_regex == ("sk-[A-Za-z0-9]{32}",)


def test_timeout_seconds_below_minimum_rejected(tmp_path: Path) -> None:
    _write_config(tmp_path, {"cross_ai": {"mode": "manual", "timeout_seconds": 5}})
    with pytest.raises(CrossAiConfigError):
        load_config(tmp_path)


def test_returned_config_is_frozen(tmp_path: Path) -> None:
    cfg = load_config(tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        cfg.mode = CrossAiMode.auto  # type: ignore[misc]


def test_disabled_mode_does_not_require_allowed_clis(tmp_path: Path) -> None:
    _write_config(tmp_path, {"cross_ai": {"mode": "disabled"}})
    cfg = load_config(tmp_path)
    assert cfg.mode is CrossAiMode.disabled
    assert cfg.allowed_clis == ()


def test_overlong_fatal_regex_also_rejected(tmp_path: Path) -> None:
    overlong = "b" * (REDACTION_REGEX_MAX_LEN + 1)
    _write_config(
        tmp_path,
        {"cross_ai": {"mode": "manual", "redaction": {"fatal_regex": [overlong]}}},
    )
    with pytest.raises(CrossAiConfigError, match="256-char"):
        load_config(tmp_path)


def test_nested_unbounded_quantifier_rejected_in_deny_regex(tmp_path: Path) -> None:
    """``(a+)+`` and friends are obvious ReDoS foot-guns; refuse at load time."""
    _write_config(
        tmp_path,
        {"cross_ai": {"mode": "manual", "redaction": {"deny_regex": ["(a+)+"]}}},
    )
    with pytest.raises(CrossAiConfigError, match="nested unbounded quantifier"):
        load_config(tmp_path)


def test_nested_unbounded_quantifier_rejected_in_fatal_regex(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        {"cross_ai": {"mode": "manual", "redaction": {"fatal_regex": ["(?:b*)*"]}}},
    )
    with pytest.raises(CrossAiConfigError, match="nested unbounded quantifier"):
        load_config(tmp_path)


def test_well_formed_user_regex_still_accepted(tmp_path: Path) -> None:
    """Regression: tightening the ReDoS guard must not over-refuse benign patterns."""
    _write_config(
        tmp_path,
        {
            "cross_ai": {
                "mode": "manual",
                "redaction": {
                    "deny_regex": [r"sk-[A-Za-z0-9]{32}"],
                    "fatal_regex": [r"-----BEGIN PRIVATE KEY-----"],
                },
            }
        },
    )
    cfg = load_config(tmp_path)
    assert cfg.redaction.deny_regex == (r"sk-[A-Za-z0-9]{32}",)
    assert cfg.redaction.fatal_regex == (r"-----BEGIN PRIVATE KEY-----",)


def test_invalid_dispatch_approved_at_timestamp_rejected(tmp_path: Path) -> None:
    """``dispatch_approved_at`` declares ``format: date-time``. The loader
    must wire up the default format checker (mirroring ``tools/state.py``)
    so a non-RFC-3339 timestamp fails at load time rather than flowing
    into the future approval-cache contract.
    """
    _write_config(
        tmp_path,
        {"cross_ai": {"mode": "manual", "dispatch_approved_at": "not-a-date"}},
    )
    with pytest.raises(CrossAiConfigError):
        load_config(tmp_path)


def test_malformed_config_json_rejected(tmp_path: Path) -> None:
    """Invalid JSON in ``.forge/config.json`` raises a typed error
    naming the parse failure (covers the ``json.JSONDecodeError`` arm of
    the loader)."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir(parents=True)
    (forge_dir / "config.json").write_text("{not valid json", encoding="utf-8")
    with pytest.raises(CrossAiConfigError, match="malformed"):
        load_config(tmp_path)


# --- to_redaction_config adapter ------------------------------------------


def test_to_redaction_config_returns_redactor_dataclass() -> None:
    """Adapter returns ``redaction.RedactionConfig`` (not ``RedactionRules``).

    The skill prose calls ``to_redaction_config`` rather than
    constructing the redactor's config by hand; the dataclass identity
    matters because ``redaction.filter`` reads ``cfg.gitignore_patterns``
    which the cross-AI config block does not surface.
    """
    rules = RedactionRules()
    adapted = to_redaction_config(rules)
    assert isinstance(adapted, redaction.RedactionConfig)


def test_to_redaction_config_preserves_user_overrides() -> None:
    """User-supplied regex / glob lists are forwarded verbatim."""
    rules = RedactionRules(
        deny_globs=("**/secrets/**",),
        deny_regex=(r"sk-[A-Za-z0-9]{16,}",),
        fatal_regex=(r"-----BEGIN PRIVATE KEY-----",),
        allow_globs=("config/example.env",),
    )
    adapted = to_redaction_config(rules)
    assert adapted.deny_regex == (r"sk-[A-Za-z0-9]{16,}",)
    assert adapted.fatal_regex == (r"-----BEGIN PRIVATE KEY-----",)
    assert adapted.allow_globs == ("config/example.env",)


def test_to_redaction_config_unions_user_deny_globs_with_defaults() -> None:
    """User deny_globs extend, never replace, the secret-shaped defaults.

    An operator who genuinely needs to forward a default-denied path
    must whitelist it via ``allow_globs``; the adapter must never let
    an empty user deny_globs silently strip the safety net.
    """
    rules = RedactionRules(deny_globs=("**/secrets/**",))
    adapted = to_redaction_config(rules)
    for default_glob in redaction.DEFAULT_DENY_GLOBS:
        assert default_glob in adapted.deny_globs
    assert "**/secrets/**" in adapted.deny_globs


def test_to_redaction_config_default_rules_keep_default_deny_globs() -> None:
    """``RedactionRules()`` (empty) yields the redactor's default deny set."""
    adapted = to_redaction_config(RedactionRules())
    assert adapted.deny_globs == redaction.DEFAULT_DENY_GLOBS


def test_to_redaction_config_dedupes_when_user_repeats_default_glob() -> None:
    """Duplicate entries collapse — order from the defaults is preserved first."""
    rules = RedactionRules(deny_globs=("**/.env", "**/custom/**"))
    adapted = to_redaction_config(rules)
    assert adapted.deny_globs.count("**/.env") == 1
    # Custom entry lands after the defaults (union order).
    assert adapted.deny_globs[-1] == "**/custom/**"


def test_to_redaction_config_accepts_gitignore_overlay() -> None:
    """Optional ``gitignore_patterns`` parameter forwards verbatim."""
    rules = RedactionRules()
    adapted = to_redaction_config(rules, gitignore_patterns=(".tmp/**",))
    assert adapted.gitignore_patterns == (".tmp/**",)

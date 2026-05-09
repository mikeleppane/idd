"""Globstar-matching coverage for tools.redaction default deny patterns.

Locks the matcher behavior so a future refactor of ``_globstar_match`` cannot
silently regress the spec-§5.3.11 default deny list.
"""

from __future__ import annotations

from pathlib import PurePosixPath

from tools import redaction


def _excluded(file_path: str) -> bool:
    payload = redaction.PromptPayload(files=(PurePosixPath(file_path),))
    return PurePosixPath(file_path) in redaction.filter(payload).excluded_files


def test_dotenv_pattern_matches_root_and_nested_variants() -> None:
    """``**/.env*`` matches .env, project/.env, deep/.env.local."""
    assert _excluded(".env")
    assert _excluded("project/.env")
    assert _excluded("a/b/c/.env.local")


def test_credentials_pattern_matches_substring_anywhere() -> None:
    """``**/*credentials*`` matches any path segment containing ``credentials``."""
    assert _excluded("foo/credentials.json")
    assert _excluded("nested/aws/credentials")
    assert _excluded("etc/my-credentials-file.txt")


def test_secrets_pattern_matches_basename_prefix() -> None:
    """``**/secrets*`` matches files whose basename starts with ``secrets``."""
    assert _excluded("foo/secrets.txt")
    assert _excluded("secrets.yml")
    assert _excluded("deep/path/secrets")


def test_aws_directory_pattern_matches_descendants() -> None:
    """``**/.aws/**`` matches anything under a .aws directory at any depth."""
    assert _excluded(".aws/credentials.bak")
    assert _excluded("home/me/.aws/config")
    assert _excluded("nested/repo/.aws/sso/cache.json")


def test_ssh_directory_pattern_matches_descendants() -> None:
    """``**/.ssh/**`` matches anything under a .ssh directory at any depth."""
    assert _excluded(".ssh/id_rsa")
    assert _excluded("home/me/.ssh/known_hosts")


def test_unrelated_paths_are_not_excluded() -> None:
    """Negative: paths that miss every default pattern survive untouched."""
    assert not _excluded("README.md")
    assert not _excluded("src/lib.py")
    assert not _excluded("docs/architecture.md")
    # ``environment`` is not ``.env``-prefixed → must not match ``**/.env*``.
    assert not _excluded("config/environment.yaml")

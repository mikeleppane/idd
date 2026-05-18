"""Registry for schema-versioned file migrations.

Every existing versioned file is treated as schema_version 1. The registry
records that baseline contract without modifying any file. schema_version and
flow_version are independent axes.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

# Version axes are intentionally separate:
# - schema_version is the per-file file-format version.
# - flow_version belongs only to the state.json lifecycle protocol.

SCHEMA_VERSION_REQUIRED_ENV = "FORGE_SCHEMA_VERSION_REQUIRED"

_FRONTMATTER_SCHEMA_SUFFIX = "-frontmatter.schema.json"
# Generic skill/command frontmatter schema is *not* a versioned FORGE artifact
# contract — it lives alongside Markdown skills/commands that are pure docs.
# Including it in the F6 axis would force a per-skill schema_version on every
# SKILL.md / command.md with no migration story.
_GENERIC_FRONTMATTER_SCHEMA = "frontmatter.schema.json"


class MigrationRegistryError(RuntimeError):
    """Raised when a schema migration cannot be registered or applied."""


@dataclass(frozen=True)
class Migration:
    """Typed schema migration between two versions for one file kind."""

    file_kind: str
    from_version: int
    to_version: int
    transform: Callable[[dict[str, Any]], dict[str, Any]]
    inverse: Callable[[dict[str, Any]], dict[str, Any]] | None = None


REGISTRY: dict[tuple[str, int], Migration] = {}


def register(migration: Migration, *, replace: bool = False) -> Migration:
    """Register a migration by file kind and source schema version.

    Args:
        migration: Migration contract to store.
        replace: When True, overwrite a registration for the same
            ``(file_kind, from_version)`` key. Defaults to False so that
            import-order accidents cannot silently swap a real ``1 -> 2``
            migration with an identity anchor (or vice versa) at startup.

    Returns:
        The same migration instance, unchanged.

    Raises:
        MigrationRegistryError: If the migration has invalid versions or
            collides with an existing registration and ``replace=False``.
    """
    _validate_migration(migration)
    key = (migration.file_kind, migration.from_version)
    existing = REGISTRY.get(key)
    if existing is not None and not replace:
        raise MigrationRegistryError(
            f"{migration.file_kind} already has a migration from schema_version "
            f"{migration.from_version} (to {existing.to_version}); pass replace=True "
            "to overwrite explicitly"
        )
    REGISTRY[key] = migration
    return migration


def apply_pending(file_kind: str, doc: dict[str, Any]) -> dict[str, Any]:
    """Apply all registered forward migrations for a file kind.

    Args:
        file_kind: File kind whose migration chain should be used.
        doc: Parsed document payload. Missing schema_version means version 1.

    Returns:
        Migrated document payload. If no migration is pending, returns a
        shallow copy of the input document.

    Raises:
        MigrationRegistryError: If the document version is invalid, newer than
            the registry knows about, or cannot reach the latest known version.
    """
    current_version = _schema_version(doc)
    latest_version = _latest_known_version(file_kind)
    current_doc = dict(doc)

    if latest_version is None:
        return current_doc
    if current_version > latest_version:
        raise MigrationRegistryError(
            f"{file_kind} schema_version {current_version} is newer than "
            f"latest registered version {latest_version}"
        )

    while current_version < latest_version:
        migration = REGISTRY.get((file_kind, current_version))
        if migration is None:
            raise MigrationRegistryError(
                f"{file_kind} migration chain is broken at schema_version "
                f"{current_version}; latest registered version is {latest_version}"
            )
        # to_version > from_version is already enforced at registration time
        # (see _validate_migration), so no in-loop re-check is needed here.

        current_doc = dict(migration.transform(dict(current_doc)))
        current_doc["schema_version"] = migration.to_version
        current_version = migration.to_version

    return current_doc


def _validate_migration(migration: Migration) -> None:
    if not migration.file_kind:
        raise MigrationRegistryError("migration file_kind must be non-empty")
    if migration.from_version < 1 or migration.to_version < 1:
        raise MigrationRegistryError("migration versions must be greater than or equal to 1")
    if migration.to_version < migration.from_version:
        raise MigrationRegistryError(
            f"{migration.file_kind} migration cannot move backward: "
            f"{migration.from_version}->{migration.to_version}"
        )
    if migration.to_version == migration.from_version and migration.from_version != 1:
        raise MigrationRegistryError(
            f"{migration.file_kind} identity migration is only valid for version 1"
        )


def parse_schema_version(value: Any) -> int:
    """Coerce a ``schema_version`` field to a positive int.

    Refuses ``bool`` explicitly even though Python treats ``bool`` as an ``int``
    subclass; a YAML ``schema_version: true`` should not be silently accepted as
    "version 1". Also refuses non-int types and values < 1.
    """
    if isinstance(value, bool):
        raise MigrationRegistryError(f"schema_version must be an integer, got bool ({value!r})")
    if not isinstance(value, int):
        raise MigrationRegistryError(
            f"schema_version must be an integer, got {type(value).__name__} ({value!r})"
        )
    if value < 1:
        raise MigrationRegistryError(f"schema_version must be >= 1, got {value}")
    return value


def _schema_version(doc: dict[str, Any]) -> int:
    return parse_schema_version(doc.get("schema_version", 1))


def latest_known_version(file_kind: str) -> int | None:
    """Maximum ``schema_version`` known to the registry for ``file_kind``.

    Returns ``None`` when no migration (identity or otherwise) has been
    registered for that kind — the caller treats that as "not in F6 scope".
    """
    versions = [
        version
        for migration in REGISTRY.values()
        if migration.file_kind == file_kind
        for version in (migration.from_version, migration.to_version)
    ]
    if not versions:
        return None
    return max(versions)


_latest_known_version = latest_known_version  # backwards-compatible alias


def file_kind_from_schema_filename(schema_filename: str) -> str | None:
    """Return the F6 file_kind for a schema filename, or ``None`` if out of scope.

    The generic skill/command frontmatter schema (``frontmatter.schema.json``)
    is intentionally out of scope: skills/commands are pure documentation files
    with no versioned-artifact contract behind them, and forcing each to declare
    a ``schema_version`` would give operators no clean migration path while
    adding none of the safety benefits of the F6 axis.
    """
    if schema_filename == _GENERIC_FRONTMATTER_SCHEMA:
        return None
    if schema_filename.endswith(_FRONTMATTER_SCHEMA_SUFFIX):
        return schema_filename.removesuffix(_FRONTMATTER_SCHEMA_SUFFIX)
    return schema_filename.removesuffix(".schema.json")


SchemaVersionSeverity = Literal["missing", "bad_type", "too_new"]


def schema_version_error(
    path: Path,
    payload: dict[str, Any],
    file_kind: str | None,
) -> tuple[SchemaVersionSeverity, str] | None:
    """Validate ``schema_version`` on a parsed payload.

    Args:
        path: Source path; included verbatim in returned messages so callers
            can surface them without re-formatting.
        payload: Parsed frontmatter dict or JSON object.
        file_kind: Result of :func:`file_kind_from_schema_filename`, or any
            other kind string the caller derives. ``None`` (or a kind that
            has no registry entry) means "not a versioned FORGE artifact";
            the check is skipped silently.

    Returns:
        ``(severity, message)`` describing the issue, or ``None`` when the
        payload is valid or out of scope. Severity is one of ``"missing"``,
        ``"bad_type"``, or ``"too_new"``.
    """
    if file_kind is None:
        return None
    latest = latest_known_version(file_kind)
    if latest is None:
        return None
    if "schema_version" not in payload:
        return (
            "missing",
            f"{path}: schema_version missing; run 'forge-state migrate' to add "
            f"it (set {SCHEMA_VERSION_REQUIRED_ENV}=1 to treat this as an error)",
        )
    try:
        version = parse_schema_version(payload["schema_version"])
    except MigrationRegistryError as exc:
        return ("bad_type", f"{path}: {exc}")
    if version > latest:
        return (
            "too_new",
            f"{path}: schema_version {version} is newer than latest registered "
            f"version {latest} for {file_kind}",
        )
    return None


def schema_version_missing_is_fatal() -> bool:
    """True when ``FORGE_SCHEMA_VERSION_REQUIRED=1`` is set in the environment."""
    return os.environ.get(SCHEMA_VERSION_REQUIRED_ENV) == "1"

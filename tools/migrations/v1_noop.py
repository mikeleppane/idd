"""Register identity migrations for known v1 file kinds.

These migrations anchor the v1 file-format contracts in the registry so known
file kinds have explicit entries without changing document payloads.
"""

from __future__ import annotations

from typing import Any

from tools.migrations.registry import Migration, register

_V1_FILE_KINDS: tuple[str, ...] = (
    "capability-spec",
    "constitution",
    "delta-proposal",
    "plan",
    "research",
    "review",
    "spec",
    "understanding",
    "conventions",
    "cross-ai-config",
    "research-config",
    "git-conventions-config",
)


def _identity(doc: dict[str, Any]) -> dict[str, Any]:
    return dict(doc)


for _file_kind in _V1_FILE_KINDS:
    register(
        Migration(
            file_kind=_file_kind,
            from_version=1,
            to_version=1,
            transform=_identity,
        )
    )

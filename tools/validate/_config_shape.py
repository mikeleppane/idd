"""``.forge/config.json`` shape validator (M8 §5.3.7 D-CONFIG).

Validates the ``cross_ai`` and ``research`` sub-blocks against their
JSON Schemas. The config file is optional: when absent the validator
returns no findings so a freshly-initialized repo passes silently.
A present-but-malformed config BLOCKS so misconfigurations cannot
leak through to skill execution.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ._finding import Finding
from ._frontmatter import _build_validator, _load_schema, _schema_version_findings

_TARGET = "config"

_SUBSCHEMA_BY_KEY: dict[str, str] = {
    "cross_ai": "cross-ai-config.schema.json",
    "research": "research-config.schema.json",
    "git_conventions": "git-conventions-config.schema.json",
}


def _validate_subblock(
    block_name: str,
    block: Any,
    schema_filename: str,
    config_path: Path,
) -> list[Finding]:
    if not isinstance(block, dict):
        return [
            Finding(
                "BLOCK",
                _TARGET,
                config_path,
                f"{block_name!r} must be a JSON object, got {type(block).__name__}",
            ),
        ]

    sv_findings = _schema_version_findings(config_path, block, schema_filename, _TARGET)
    if sv_findings:
        # Prefix the BLOCK message with the subblock name so the operator can
        # tell which subblock (cross_ai vs research vs git_conventions) is
        # forward-versioned. Reuses the registry-formatted body verbatim.
        return [
            Finding("BLOCK", _TARGET, config_path, f"{block_name}: {f.message}")
            for f in sv_findings
        ]

    schema = _load_schema(schema_filename)
    return [
        Finding(
            "BLOCK",
            _TARGET,
            config_path,
            f"{block_name}{('.' + str(err.path[-1])) if err.path else ''}: {err.message}",
        )
        for err in sorted(_build_validator(schema).iter_errors(block), key=lambda e: list(e.path))
    ]


def validate_config(config_path: Path) -> list[Finding]:
    """Validate ``.forge/config.json`` shape.

    Args:
        config_path: Path to the config file. Missing file is allowed
            (returns no findings); the config artifact is optional.

    Returns:
        List of Finding records. Empty list means valid (or absent).
    """
    if not config_path.is_file():
        return []

    try:
        raw = config_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        return [
            Finding(
                "BLOCK",
                _TARGET,
                config_path,
                f"failed to parse JSON: {exc}",
            ),
        ]

    if not isinstance(payload, dict):
        return [
            Finding(
                "BLOCK",
                _TARGET,
                config_path,
                f"config root must be a JSON object, got {type(payload).__name__}",
            ),
        ]

    findings: list[Finding] = []
    for key, schema_filename in _SUBSCHEMA_BY_KEY.items():
        if key in payload:
            findings.extend(_validate_subblock(key, payload[key], schema_filename, config_path))
    return findings

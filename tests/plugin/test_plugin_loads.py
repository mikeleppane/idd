"""Contract tests for the FORGE plugin manifest at .claude-plugin/plugin.json."""

from __future__ import annotations

import json
from pathlib import Path


def test_manifest_does_not_redeclare_auto_loaded_hooks(repo_root: Path) -> None:
    manifest = json.loads(
        (repo_root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    declared = manifest.get("hooks")
    if declared is None:
        return
    # Manifest may reference ADDITIONAL hook files only; the standard
    # path is auto-loaded by Claude Code and redeclaration causes
    # 'Duplicate hooks file detected' on install.
    assert isinstance(declared, list), (
        "manifest.hooks must be a list of additional files; bare string "
        "redeclares the auto-loaded standard path"
    )
    normalized = {str(Path(p)) for p in declared}
    assert "hooks/hooks.json" not in normalized
    assert Path("hooks/hooks.json") not in {Path(p) for p in declared}

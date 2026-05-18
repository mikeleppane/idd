#!/usr/bin/env python3
"""PreToolUse hook enforcing proposed-content conventions.

Reads Claude Code hook input on stdin. For ``Write`` / ``Edit`` /
``MultiEdit`` tool calls, reconstructs the proposed post-write file body
without modifying disk, evaluates ``scope="diff"`` conventions, and blocks
``BLOCK`` severity matches.

Stdlib-only plus the project-local ``tools.conventions_runtime`` module.
The local import is intentionally deferred until after cheap no-op checks so
unrelated tool calls do not pay for convention loading.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

_HOOK_ROOT = Path(__file__).resolve().parent.parent
if str(_HOOK_ROOT) not in sys.path:
    sys.path.insert(0, str(_HOOK_ROOT))

_WRITE_TOOL_NAMES = frozenset({"Write", "Edit", "MultiEdit"})
_STATE_JSON_RE = re.compile(r"(?:^|[\\/])\.forge[\\/]features[\\/][^\\/]+[\\/]state\.json$")
_ALLOW = 0
_DENY = 2


def _warn(message: str) -> None:
    print(f"WARN: {message}", file=sys.stderr)


def _is_feature_state_path(file_path: str) -> bool:
    """Return True for ``.forge/features/<id>/state.json`` paths."""
    if not file_path:
        return False
    return bool(_STATE_JSON_RE.search(file_path))


def _read_current(file_path: str) -> str | None:
    """Return current file text, or ``None`` with a warning on failure."""
    path = Path(file_path)
    if not path.is_file():
        _warn(f"cannot reconstruct proposed content; file does not exist: {file_path}")
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        _warn(f"cannot reconstruct proposed content for {file_path}: {exc}")
        return None


def _replace_once_or_all(
    content: str,
    *,
    old_string: str,
    new_string: str,
    replace_all: bool,
    file_path: str,
) -> str | None:
    """Apply one Edit-style replacement, warning and returning None on miss."""
    if old_string not in content:
        _warn(f"cannot reconstruct proposed content; old_string not found in {file_path}")
        return None
    count = -1 if replace_all else 1
    return content.replace(old_string, new_string, count)


def _string_field(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if isinstance(value, str):
        return value
    return None


def _proposed_write(tool_input: dict[str, Any], file_path: str) -> str | None:
    content = _string_field(tool_input, "content")
    if content is None:
        _warn(f"missing or non-string content for Write target {file_path}")
    return content


def _proposed_edit(tool_input: dict[str, Any], file_path: str) -> str | None:
    current = _read_current(file_path)
    if current is None:
        return None
    old_string = _string_field(tool_input, "old_string")
    new_string = _string_field(tool_input, "new_string")
    if old_string is None or new_string is None:
        _warn(f"missing or non-string old_string/new_string for Edit target {file_path}")
        return None
    return _replace_once_or_all(
        current,
        old_string=old_string,
        new_string=new_string,
        replace_all=tool_input.get("replace_all") is True,
        file_path=file_path,
    )


def _proposed_multiedit(tool_input: dict[str, Any], file_path: str) -> str | None:
    current = _read_current(file_path)
    if current is None:
        return None
    edits = tool_input.get("edits")
    if not isinstance(edits, list):
        _warn(f"missing or non-list edits for MultiEdit target {file_path}")
        return None

    proposed = current
    for index, raw_edit in enumerate(edits):
        if not isinstance(raw_edit, dict):
            _warn(f"cannot reconstruct proposed content; edit {index} is not an object")
            return None
        old_string = _string_field(raw_edit, "old_string")
        new_string = _string_field(raw_edit, "new_string")
        if old_string is None or new_string is None:
            _warn(
                "cannot reconstruct proposed content; "
                f"edit {index} missing string old_string/new_string",
            )
            return None
        next_content = _replace_once_or_all(
            proposed,
            old_string=old_string,
            new_string=new_string,
            replace_all=raw_edit.get("replace_all") is True,
            file_path=file_path,
        )
        if next_content is None:
            _warn(f"MultiEdit edit {index} could not be applied")
            return None
        proposed = next_content
    return proposed


def _proposed_content(tool_name: str, tool_input: dict[str, Any], file_path: str) -> str | None:
    if tool_name == "Write":
        return _proposed_write(tool_input, file_path)
    if tool_name == "Edit":
        return _proposed_edit(tool_input, file_path)
    if tool_name == "MultiEdit":
        return _proposed_multiedit(tool_input, file_path)
    return None


def _match_location(match: Any) -> str:
    matched_line = getattr(match, "matched_line", "")
    if isinstance(matched_line, str) and matched_line:
        return f"matched line: {matched_line}"
    file_path = getattr(match, "file_path", "")
    if isinstance(file_path, str) and file_path:
        return f"path: {file_path}"
    return "path: <unknown>"


def _format_match(match: Any) -> str:
    rule_id = getattr(match, "id", "<unknown>")
    severity = getattr(match, "severity", "<unknown>")
    return (
        f"rule id: {rule_id}; severity: {severity}; {_match_location(match)}. "
        "Fix the proposed file content so it satisfies the convention, "
        "or update .forge/conventions.json if the rule is wrong."
    )


def _runtime_evaluate() -> Any | None:
    try:
        module = __import__("tools.conventions_runtime", fromlist=["evaluate"])
    except ImportError as exc:
        _warn(f"tools.conventions_runtime could not be imported: {exc}")
        return None
    evaluate_conventions = getattr(module, "evaluate", None)
    if not callable(evaluate_conventions):
        _warn("tools.conventions_runtime.evaluate is not callable")
        return None
    return evaluate_conventions


def _evaluate(file_path: str, content: str) -> tuple[int, list[str]]:
    evaluate_conventions = _runtime_evaluate()
    if evaluate_conventions is None:
        return _ALLOW, []

    try:
        result = evaluate_conventions(scope="diff", file_path=file_path, content=content)
    except (OSError, ValueError, TypeError) as exc:
        _warn(f"conventions evaluation failed; fail-open: {exc}")
        return _ALLOW, []
    if result.load_error is not None:
        _warn(
            ".forge/conventions.json could not be evaluated; "
            f"fail-open so it can be fixed: {result.load_error}",
        )
        return _ALLOW, []

    high_messages: list[str] = []
    for match in result.matches:
        message = _format_match(match)
        if getattr(match, "severity", "") == "BLOCK":
            return _DENY, [message]
        if getattr(match, "severity", "") == "HIGH":
            high_messages.append(message)
    return _ALLOW, high_messages


def _load_payload() -> dict[str, Any] | None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        _warn(f"hook input was not valid JSON: {exc}")
        return None

    if not isinstance(payload, dict):
        _warn("hook input root was not a JSON object")
        return None
    return payload


def _request_from_payload(payload: dict[str, Any]) -> tuple[str, dict[str, Any], str] | None:
    """Return the write request tuple to evaluate, or None for fail-open/no-op."""
    tool_name = payload.get("tool_name")
    if not isinstance(tool_name, str) or tool_name not in _WRITE_TOOL_NAMES:
        return None

    raw_tool_input = payload.get("tool_input")
    if not isinstance(raw_tool_input, dict):
        _warn(f"missing or malformed tool_input for {tool_name}")
        return None

    file_path = _string_field(raw_tool_input, "file_path")
    if file_path is None:
        _warn(f"missing or non-string file_path for {tool_name}")
        return None
    if _is_feature_state_path(file_path):
        return None
    return tool_name, raw_tool_input, file_path


def main() -> int:
    """Read stdin JSON, evaluate conventions, and return hook exit status."""
    payload = _load_payload()
    if payload is None:
        return _ALLOW

    request = _request_from_payload(payload)
    if request is None:
        return _ALLOW

    tool_name, raw_tool_input, file_path = request

    content = _proposed_content(tool_name, raw_tool_input, file_path)
    if content is None:
        return _ALLOW

    status, messages = _evaluate(file_path, content)

    for message in messages:
        if status == _DENY:
            print(f"DENY: {message}", file=sys.stderr)
        else:
            _warn(message)
    return status


if __name__ == "__main__":
    raise SystemExit(main())

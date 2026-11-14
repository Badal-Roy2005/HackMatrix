"""Safe tools for the CI Doctor investigation agent.

Each tool takes a :class:`ToolContext` and returns a size-capped, redacted
string. All tool outputs are safe to feed back to an AI model.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cidoctor.core.redact import redact

# Default caps.
DEFAULT_OUTPUT_CAP = 4000  # characters
DEFAULT_READ_CAP = 100 * 1024  # bytes (100 KB)

# Denied path patterns for read_file.
_DENIED_NAMES = {".env", "id_rsa"}
_DENIED_SUFFIXES = (".pem",)
_DENIED_DIRS = (".git",)


@dataclass
class ToolContext:
    """Context carried by every tool invocation."""

    store: Any
    repo_root: Path
    run_id: str


@dataclass
class ToolCall:
    """A requested tool invocation."""

    name: str
    args: dict[str, Any]


@dataclass
class ToolCallRecord:
    """A recorded tool invocation and its (redacted, capped) output."""

    name: str
    args: dict[str, Any]
    output: str


def tool_output(text: str, cap: int = DEFAULT_OUTPUT_CAP) -> str:
    """Redact and cap tool output."""
    return redact(text)[:cap]


def _is_hex_sha(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{4,40}", value))


def _is_valid_commit_spec(value: str) -> bool:
    """Allow HEAD, HEAD~N, or a 4-40 character hex SHA."""
    if value == "HEAD":
        return True
    head_n = re.fullmatch(r"HEAD~\d+", value)
    if head_n:
        return True
    return _is_hex_sha(value)


def get_failure_groups(ctx: ToolContext, run_id: str) -> str:
    """Return failure groups for a run as a JSON string."""
    try:
        run = ctx.store.get_run(run_id)
    except Exception as exc:  # noqa: BLE001 - tool boundary
        return tool_output(f"error getting run {run_id}: {exc}")

    if run is None:
        return tool_output(f"run {run_id} not found")

    from cidoctor.core.fingerprint import group_failures

    groups = group_failures(run.results)
    payload = [
        {
            "fingerprint": g.fingerprint,
            "count": g.count,
            "sample_message": g.sample_message,
            "tests": g.tests,
        }
        for g in groups
    ]
    return tool_output(json.dumps(payload, indent=2, default=str))


def get_test_history(ctx: ToolContext, test_name: str, days: int = 14) -> str:
    """Return recent history for a test as a JSON string."""
    from datetime import UTC, datetime, timedelta

    since = datetime.now(UTC) - timedelta(days=days)
    try:
        history = ctx.store.test_history(test_name, since=since)
    except Exception as exc:  # noqa: BLE001 - tool boundary
        return tool_output(f"error getting history for {test_name}: {exc}")

    return tool_output(json.dumps(history, indent=2, default=str))


def get_git_diff(ctx: ToolContext, commit: str) -> str:
    """Return the git diff for a commit spec.

    Only ``HEAD``, ``HEAD~N``, or a 4-40 character hex SHA is allowed. Git is
    invoked with an argument list (no shell). Output is size-capped.
    """
    if not _is_valid_commit_spec(commit):
        return tool_output(
            f"invalid commit spec {commit!r}: use HEAD, HEAD~N, or a hex SHA"
        )

    repo_root = ctx.repo_root
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "diff", commit],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return tool_output(f"git diff failed for {commit}: {exc}")

    combined = result.stdout
    if result.stderr:
        combined += "\nSTDERR:\n" + result.stderr
    return tool_output(combined)


def read_file(ctx: ToolContext, path: str, start_line: int = 1, end_line: int = 0) -> str:
    """Read a file within repo_root, with safety restrictions.

    * Resolves the path and requires it inside repo_root.
    * Rejects symlinks that escape repo_root.
    * Denies .env, .git/, *.pem and id_rsa.
    * Caps output at 100 KB.
    """
    repo_root = ctx.repo_root.resolve()

    # Deny by name.
    name = Path(path).name
    if name in _DENIED_NAMES or name.endswith(_DENIED_SUFFIXES):
        return tool_output(f"denied: {name} is not readable")
    for denied_dir in _DENIED_DIRS:
        if f"/{denied_dir}/" in path or path.startswith(denied_dir + "/"):
            return tool_output(f"denied: {denied_dir} is not readable")

    candidate = (repo_root / path).resolve()
    try:
        candidate.relative_to(repo_root)
    except ValueError:
        return tool_output(f"denied: {path} escapes repository root")

    # Reject symlinks that escape.
    if candidate.is_symlink():
        link_target = os.readlink(candidate)
        resolved_target = Path(link_target)
        if not resolved_target.is_absolute():
            resolved_target = (candidate.parent / resolved_target).resolve()
        try:
            resolved_target.relative_to(repo_root)
        except ValueError:
            return tool_output(f"denied: symlink {path} escapes repository root")

    if not candidate.is_file():
        return tool_output(f"not a file: {path}")

    try:
        data = candidate.read_bytes()[:DEFAULT_READ_CAP]
    except OSError as exc:
        return tool_output(f"error reading {path}: {exc}")

    text = data.decode("utf-8", errors="replace")

    # Apply line range.
    start_line = max(start_line, 1)
    lines = text.splitlines()
    if end_line and end_line > 0:
        lines = lines[start_line - 1 : end_line]
    else:
        lines = lines[start_line - 1 :]

    return tool_output("\n".join(lines))


# Tool name -> (function, JSON schema declaration).


def _get_failure_groups_schema() -> dict[str, Any]:
    return {
        "name": "get_failure_groups",
        "description": "Return failure groups (fingerprint, count, sample message, tests) for a CI run.",
        "parameters": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "The run id to inspect."}
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
    }


def _get_test_history_schema() -> dict[str, Any]:
    return {
        "name": "get_test_history",
        "description": "Return recent run history for a test (pass/fail status per run).",
        "parameters": {
            "type": "object",
            "properties": {
                "test_name": {"type": "string", "description": "Name of the test."},
                "days": {
                    "type": "integer",
                    "description": "Look back this many days (default 14).",
                },
            },
            "required": ["test_name"],
            "additionalProperties": False,
        },
    }


def _get_git_diff_schema() -> dict[str, Any]:
    return {
        "name": "get_git_diff",
        "description": "Return the git diff for a commit spec (HEAD, HEAD~N, or hex SHA).",
        "parameters": {
            "type": "object",
            "properties": {
                "commit": {
                    "type": "string",
                    "description": "Commit spec: HEAD, HEAD~N, or a 4-40 hex SHA.",
                }
            },
            "required": ["commit"],
            "additionalProperties": False,
        },
    }


def _read_file_schema() -> dict[str, Any]:
    return {
        "name": "read_file",
        "description": "Read a file within the repository root (line range optional).",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path relative to repo root."},
                "start_line": {
                    "type": "integer",
                    "description": "First line to read (1-based, default 1).",
                },
                "end_line": {
                    "type": "integer",
                    "description": "Last line to read (inclusive, default 0 = to end).",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    }


TOOL_DECLARATIONS: list[dict[str, Any]] = [
    _get_failure_groups_schema(),
    _get_test_history_schema(),
    _get_git_diff_schema(),
    _read_file_schema(),
]

ToolFunc = Callable[..., str]

_TOOL_FUNCTIONS: dict[str, ToolFunc] = {
    "get_failure_groups": get_failure_groups,
    "get_test_history": get_test_history,
    "get_git_diff": get_git_diff,
    "read_file": read_file,
}


def call_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> str:
    """Dispatch a tool call by name, returning a capped redacted output."""
    fn = _TOOL_FUNCTIONS.get(name)
    if fn is None:
        return tool_output(f"error: unknown tool {name!r}")
    return fn(ctx, **args)

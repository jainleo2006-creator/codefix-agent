"""Terminal execution tool. Controlled, not "run whatever the model said".

Spec rule 22: allowlist/denylist, cwd restriction, timeout, output limits,
confirmation for dangerous commands. This slice implements the allowlist,
cwd restriction, timeout, and output limits for real; the interactive
confirmation flow (permission modes SAFE/NORMAL/AUTO) is a TODO for the
full orchestrator and is intentionally not faked here.
"""
from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

from .base import Tool, ToolResult

MAX_OUTPUT_CHARS = 20_000
DEFAULT_TIMEOUT_SECONDS = 30

# Deliberately small allowlist for this slice. Extend via config, not by
# loosening this default - see spec rule 23 (permission system).
DEFAULT_ALLOWED_COMMANDS = frozenset({"pytest", "python", "python3", "pip", "ruff", "mypy", "git", "ls", "cat"})

DENYLIST_SUBSTRINGS = ("rm -rf", "sudo", ":(){:|:&};:", "> /dev/sda", "mkfs", "dd if=")


class CommandNotAllowedError(Exception):
    pass


class RunCommandTool(Tool):
    name = "run_command"
    description = (
        "Run a shell command inside the project directory. Only allowlisted "
        "commands are permitted (pytest, python, pip, ruff, mypy, git, ls, cat)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The command to run, e.g. 'pytest -q'."},
            "timeout_seconds": {"type": "integer", "description": "Max seconds to allow (default 30)."},
        },
        "required": ["command"],
    }

    def __init__(
        self,
        project_root: str | Path,
        allowed_commands: frozenset[str] = DEFAULT_ALLOWED_COMMANDS,
    ):
        self.project_root = Path(project_root).resolve()
        self.allowed_commands = allowed_commands

    def _check_allowed(self, command: str) -> None:
        lowered = command.lower()
        for bad in DENYLIST_SUBSTRINGS:
            if bad in lowered:
                raise CommandNotAllowedError(f"Command matches denylist pattern: '{bad}'")

        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            raise CommandNotAllowedError(f"Could not parse command: {exc}") from exc

        if not tokens:
            raise CommandNotAllowedError("Empty command.")

        binary = tokens[0]
        if binary not in self.allowed_commands:
            raise CommandNotAllowedError(
                f"'{binary}' is not in the allowlist ({sorted(self.allowed_commands)})."
            )

    def execute(self, command: str, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> ToolResult:
        try:
            self._check_allowed(command)
        except CommandNotAllowedError as exc:
            return ToolResult(success=False, output="", error=str(exc))

        try:
            proc = subprocess.run(
                shlex.split(command),
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                success=False, output="", error=f"Command timed out after {timeout_seconds}s."
            )
        except OSError as exc:
            return ToolResult(success=False, output="", error=f"Failed to execute: {exc}")

        combined = (proc.stdout or "") + (proc.stderr or "")
        truncated = len(combined) > MAX_OUTPUT_CHARS
        if truncated:
            combined = combined[:MAX_OUTPUT_CHARS] + "\n[... output truncated ...]"

        return ToolResult(
            success=proc.returncode == 0,
            output=combined,
            error=None if proc.returncode == 0 else f"Exit code {proc.returncode}",
        )

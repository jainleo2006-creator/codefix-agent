"""File tools. Real filesystem access, restricted to a project root."""
from __future__ import annotations

from pathlib import Path

from .base import Tool, ToolResult

MAX_READ_BYTES = 200_000


class ReadFileTool(Tool):
    name = "read_file"
    description = "Read the contents of a text file within the project directory."
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file, relative to the project root.",
            }
        },
        "required": ["path"],
    }

    def __init__(self, project_root: str | Path):
        self.project_root = Path(project_root).resolve()

    def _resolve_safe(self, rel_path: str) -> Path | None:
        candidate = (self.project_root / rel_path).resolve()
        try:
            candidate.relative_to(self.project_root)
        except ValueError:
            return None  # path traversal attempt (e.g. "../../etc/passwd")
        return candidate

    def execute(self, path: str) -> ToolResult:
        resolved = self._resolve_safe(path)
        if resolved is None:
            return ToolResult(
                success=False,
                output="",
                error=f"Refused: '{path}' resolves outside the project root.",
            )
        if not resolved.exists():
            return ToolResult(success=False, output="", error=f"File not found: {path}")
        if not resolved.is_file():
            return ToolResult(success=False, output="", error=f"Not a file: {path}")

        try:
            data = resolved.read_bytes()
        except OSError as exc:
            return ToolResult(success=False, output="", error=f"Read failed: {exc}")

        truncated = False
        if len(data) > MAX_READ_BYTES:
            data = data[:MAX_READ_BYTES]
            truncated = True

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return ToolResult(success=False, output="", error=f"'{path}' is not a UTF-8 text file.")

        if truncated:
            text += f"\n\n[... truncated at {MAX_READ_BYTES} bytes ...]"

        return ToolResult(success=True, output=text)

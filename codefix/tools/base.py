"""Tool abstraction. Every tool must declare a JSON-schema signature (so it
can be handed to any provider unchanged) and return a ToolResult - never
raise raw exceptions past `execute`.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any

from ..providers.base import ToolDefinition


@dataclass
class ToolResult:
    success: bool
    output: str
    error: str | None = None


class Tool(abc.ABC):
    name: str
    description: str
    parameters: dict[str, Any]

    def definition(self) -> ToolDefinition:
        return ToolDefinition(name=self.name, description=self.description, parameters=self.parameters)

    @abc.abstractmethod
    def execute(self, **kwargs: Any) -> ToolResult:
        ...

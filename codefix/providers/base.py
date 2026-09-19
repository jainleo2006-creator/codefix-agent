"""Provider-agnostic core types.

Every AI provider adapter must normalize its wire format into the dataclasses
defined here. No provider-specific object should ever leak past this module's
boundary into the router, orchestrator, or CLI.
"""
from __future__ import annotations

import abc
import enum
from dataclasses import dataclass, field
from typing import Any


class ErrorType(str, enum.Enum):
    """Canonical failure classification, independent of provider wire format."""

    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    RATE_LIMITED = "RATE_LIMITED"
    TIMEOUT = "TIMEOUT"
    SERVER_ERROR = "SERVER_ERROR"
    TEMPORARY_UNAVAILABLE = "TEMPORARY_UNAVAILABLE"
    CONTEXT_LIMIT = "CONTEXT_LIMIT"
    PROVIDER_OVERLOADED = "PROVIDER_OVERLOADED"
    INVALID_API_KEY = "INVALID_API_KEY"
    MISSING_API_KEY = "MISSING_API_KEY"
    INVALID_CONFIGURATION = "INVALID_CONFIGURATION"
    UNKNOWN = "UNKNOWN"


# Error types where failing over to another provider is appropriate.
# Auth/config errors are deliberately excluded: switching providers won't
# fix a bad key, and it wastes calls against providers that were never
# going to work either (see rule 9 in the design spec).
RETRYABLE_ERROR_TYPES = frozenset(
    {
        ErrorType.QUOTA_EXCEEDED,
        ErrorType.RATE_LIMITED,
        ErrorType.TIMEOUT,
        ErrorType.SERVER_ERROR,
        ErrorType.TEMPORARY_UNAVAILABLE,
        ErrorType.PROVIDER_OVERLOADED,
        ErrorType.CONTEXT_LIMIT,
    }
)


class ProviderError(Exception):
    """Raised by any AIProvider. Always carries a canonical ErrorType."""

    def __init__(self, error_type: ErrorType, message: str, provider: str, raw: Any = None):
        self.error_type = error_type
        self.provider = provider
        self.raw = raw
        super().__init__(f"[{provider}] {error_type.value}: {message}")


@dataclass(frozen=True)
class Capabilities:
    """What a given provider/model can actually do. The router must check
    this before assigning work that needs a capability the model lacks."""

    tool_calling: bool = False
    streaming: bool = False
    vision: bool = False
    long_context: bool = False
    structured_output: bool = False
    reasoning: bool = False
    code_generation: bool = True
    mcp: bool = False

    def supports(self, *required: str) -> bool:
        return all(getattr(self, r, False) for r in required)


@dataclass
class ToolCall:
    """Normalized tool call, regardless of provider wire format."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolDefinition:
    """Normalized tool schema handed to a provider."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema for the tool's input


@dataclass
class AgentResponse:
    """Canonical response shape. The orchestrator/router only ever sees this,
    never a raw OpenAI/Anthropic/Gemini response object."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"  # "stop" | "tool_use" | "length" | "error"
    usage: dict[str, int] = field(default_factory=dict)
    provider: str = ""
    model: str = ""
    request_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Message:
    role: str  # "user" | "assistant" | "tool"
    content: str
    tool_call_id: str | None = None  # set when role == "tool"
    name: str | None = None  # tool name, when role == "tool"


class AIProvider(abc.ABC):
    """Universal provider interface. Every concrete provider adapter must
    implement `complete` and normalize its own errors via `classify_error`."""

    name: str = "unknown"

    def __init__(self, model: str):
        self.model = model

    @property
    @abc.abstractmethod
    def capabilities(self) -> Capabilities:
        ...

    @abc.abstractmethod
    def complete(
        self,
        messages: list[Message],
        system: str | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> AgentResponse:
        """Send a request and return a normalized AgentResponse.

        Must raise ProviderError (never a raw SDK exception) on failure.
        """
        ...

    def is_configured(self) -> bool:
        """Whether this provider has what it needs (e.g. an API key) to
        even attempt a request. Checked before use, not just on failure."""
        return True

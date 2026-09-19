"""Real Anthropic provider adapter.

This makes actual network calls via the `anthropic` SDK. Requires
ANTHROPIC_API_KEY to be set in the environment.
"""
from __future__ import annotations

import os
from typing import Any

from .base import (
    AgentResponse,
    AIProvider,
    Capabilities,
    ErrorType,
    Message,
    ProviderError,
    ToolCall,
    ToolDefinition,
)

DEFAULT_MODEL = "claude-sonnet-4-6"


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None):
        super().__init__(model)
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self._client = None  # lazily constructed so import cost / missing key
        # doesn't blow up until the provider is actually used.

    def is_configured(self) -> bool:
        return bool(self._api_key)

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            tool_calling=True,
            streaming=True,
            vision=True,
            long_context=True,
            structured_output=True,
            reasoning=True,
            code_generation=True,
            mcp=True,
        )

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise ProviderError(
                    ErrorType.INVALID_CONFIGURATION,
                    "The 'anthropic' package is not installed. Run: pip install anthropic",
                    self.name,
                    raw=exc,
                ) from exc
            if not self._api_key:
                raise ProviderError(
                    ErrorType.MISSING_API_KEY,
                    "ANTHROPIC_API_KEY is not set. Run: codefix config",
                    self.name,
                )
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    @staticmethod
    def _to_anthropic_tools(tools: list[ToolDefinition] | None) -> list[dict[str, Any]] | None:
        if not tools:
            return None
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
            }
            for t in tools
        ]

    @staticmethod
    def _to_anthropic_messages(messages: list[Message]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "tool":
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": m.tool_call_id,
                                "content": m.content,
                            }
                        ],
                    }
                )
            else:
                out.append({"role": m.role, "content": m.content})
        return out

    def complete(
        self,
        messages: list[Message],
        system: str | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> AgentResponse:
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": self._to_anthropic_messages(messages),
        }
        if system:
            kwargs["system"] = system
        anthropic_tools = self._to_anthropic_tools(tools)
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        try:
            resp = client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - normalized below
            raise ProviderError(self._classify_error(exc), str(exc), self.name, raw=exc) from exc

        return self._normalize(resp)

    def _normalize(self, resp: Any) -> AgentResponse:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=block.input))

        finish_reason = "tool_use" if tool_calls else "stop"
        if getattr(resp, "stop_reason", None) == "max_tokens":
            finish_reason = "length"

        usage = {}
        if getattr(resp, "usage", None) is not None:
            usage = {
                "input_tokens": resp.usage.input_tokens,
                "output_tokens": resp.usage.output_tokens,
            }

        return AgentResponse(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
            provider=self.name,
            model=self.model,
            request_id=getattr(resp, "id", None),
        )

    @staticmethod
    def _classify_error(exc: Exception) -> ErrorType:
        try:
            import anthropic
        except ImportError:
            return ErrorType.UNKNOWN

        if isinstance(exc, anthropic.AuthenticationError):
            return ErrorType.INVALID_API_KEY
        if isinstance(exc, anthropic.RateLimitError):
            return ErrorType.RATE_LIMITED
        if isinstance(exc, anthropic.APITimeoutError):
            return ErrorType.TIMEOUT
        if isinstance(exc, anthropic.APIConnectionError):
            return ErrorType.TEMPORARY_UNAVAILABLE
        if isinstance(exc, anthropic.APIStatusError):
            if exc.status_code == 429:
                return ErrorType.RATE_LIMITED
            if exc.status_code == 529:
                return ErrorType.PROVIDER_OVERLOADED
            if exc.status_code in (500, 502, 503, 504):
                return ErrorType.SERVER_ERROR
            if exc.status_code == 400 and "context" in str(exc).lower():
                return ErrorType.CONTEXT_LIMIT
            if exc.status_code in (401, 403):
                return ErrorType.INVALID_API_KEY
        return ErrorType.UNKNOWN

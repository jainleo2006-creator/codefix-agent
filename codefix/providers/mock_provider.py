"""Deterministic mock provider for `codefix demo` and offline tests.

This NEVER makes a network call and NEVER pretends to be a real model
response. It is used only for the zero-config demo path and for unit tests
so the suite doesn't require paid API access (spec rules 37 and 50/52).
"""
from __future__ import annotations

from .base import AgentResponse, AIProvider, Capabilities, Message, ToolCall, ToolDefinition


class MockProvider(AIProvider):
    """Scripted provider. Given a prompt containing a filename, it will
    first call read_file on that file, then summarize the (real) tool
    result it gets back. Everything it does is honest about being a demo.
    """

    name = "mock"

    def __init__(self, model: str = "mock-demo-1", fail_with: str | None = None):
        super().__init__(model)
        self._fail_with = fail_with  # ErrorType name, for testing failover
        self._step = 0

    @property
    def capabilities(self) -> Capabilities:
        return Capabilities(
            tool_calling=True,
            streaming=False,
            vision=False,
            long_context=False,
            structured_output=True,
            reasoning=False,
            code_generation=True,
            mcp=False,
        )

    def complete(
        self,
        messages: list[Message],
        system: str | None = None,
        tools: list[ToolDefinition] | None = None,
    ) -> AgentResponse:
        if self._fail_with:
            from .base import ErrorType, ProviderError

            raise ProviderError(ErrorType(self._fail_with), "simulated failure", self.name)

        last_tool_result = next(
            (m for m in reversed(messages) if m.role == "tool"), None
        )

        if last_tool_result is None and tools:
            # First turn: call the first available tool on whatever file-like
            # token we can find in the latest user message, defaulting to a
            # sample file so the demo works out of the box.
            target = "sample_project/app.py"
            for m in reversed(messages):
                if m.role == "user" and ".py" in m.content:
                    for token in m.content.split():
                        if token.endswith(".py"):
                            target = token
                            break
                    break
            tool = tools[0]
            return AgentResponse(
                text="[DEMO MODE] Inspecting the file before proposing anything.",
                tool_calls=[ToolCall(id="mock-call-1", name=tool.name, arguments={"path": target})],
                finish_reason="tool_use",
                usage={"input_tokens": 0, "output_tokens": 0},
                provider=self.name,
                model=self.model,
            )

        # Second turn: we already have a real tool result, summarize it.
        snippet = (last_tool_result.content or "")[:200] if last_tool_result else ""
        return AgentResponse(
            text=(
                "[DEMO MODE - no real model was called] "
                f"Read {len(snippet)} chars from the target file. "
                "A real provider (e.g. Anthropic) would analyze this content and "
                "propose a fix here. Configure ANTHROPIC_API_KEY and use "
                "--provider anthropic for real analysis."
            ),
            tool_calls=[],
            finish_reason="stop",
            usage={"input_tokens": 0, "output_tokens": 0},
            provider=self.name,
            model=self.model,
        )

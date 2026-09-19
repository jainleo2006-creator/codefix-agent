from codefix.providers.base import AgentResponse, Capabilities, Message, ProviderError, ErrorType
from codefix.providers.mock_provider import MockProvider


def test_mock_provider_capabilities():
    p = MockProvider()
    assert p.capabilities.tool_calling is True
    assert p.capabilities.supports("tool_calling") is True
    assert p.capabilities.supports("vision") is False


def test_mock_provider_first_turn_calls_tool():
    p = MockProvider()
    resp = p.complete([Message(role="user", content="check app.py please")], tools=[
        __import__("codefix.providers.base", fromlist=["ToolDefinition"]).ToolDefinition(
            name="read_file", description="read", parameters={}
        )
    ])
    assert isinstance(resp, AgentResponse)
    assert resp.finish_reason == "tool_use"
    assert resp.tool_calls[0].name == "read_file"
    assert resp.tool_calls[0].arguments["path"].endswith("app.py")


def test_mock_provider_second_turn_summarizes():
    p = MockProvider()
    messages = [
        Message(role="user", content="check app.py"),
        Message(role="tool", content="def add(a, b): return a - b", tool_call_id="x", name="read_file"),
    ]
    resp = p.complete(messages, tools=[])
    assert resp.finish_reason == "stop"
    assert "DEMO MODE" in resp.text


def test_mock_provider_simulated_failure_raises_provider_error():
    p = MockProvider(fail_with="RATE_LIMITED")
    try:
        p.complete([Message(role="user", content="hi")])
        assert False, "expected ProviderError"
    except ProviderError as exc:
        assert exc.error_type == ErrorType.RATE_LIMITED
        assert exc.provider == "mock"

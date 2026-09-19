import pytest

from codefix.providers.base import Message
from codefix.providers.mock_provider import MockProvider
from codefix.router import NoProviderAvailableError, ProviderRouter, RoutingMode


def test_auto_mode_fails_over_on_retryable_error():
    flaky = MockProvider(fail_with="RATE_LIMITED")
    flaky.name = "flaky"
    healthy = MockProvider()
    healthy.name = "healthy"

    router = ProviderRouter([flaky, healthy], mode=RoutingMode.AUTO)
    result = router.complete([Message(role="user", content="hi")])

    assert result.provider_used == "healthy"
    assert len(result.failover_events) == 1
    assert result.failover_events[0].from_provider == "flaky"
    assert result.failover_events[0].to_provider == "healthy"
    assert result.failover_events[0].error_type == "RATE_LIMITED"


def test_auto_mode_does_not_fail_over_on_auth_error():
    bad_key = MockProvider(fail_with="INVALID_API_KEY")
    bad_key.name = "bad_key"
    healthy = MockProvider()
    healthy.name = "healthy"

    router = ProviderRouter([bad_key, healthy], mode=RoutingMode.AUTO)
    with pytest.raises(Exception):
        router.complete([Message(role="user", content="hi")])


def test_manual_mode_never_fails_over():
    flaky = MockProvider(fail_with="RATE_LIMITED")
    flaky.name = "flaky"
    healthy = MockProvider()
    healthy.name = "healthy"

    router = ProviderRouter([flaky, healthy], mode=RoutingMode.MANUAL)
    with pytest.raises(Exception):
        router.complete([Message(role="user", content="hi")], provider_name="flaky")


def test_no_provider_available_when_all_fail():
    a = MockProvider(fail_with="SERVER_ERROR")
    a.name = "a"
    b = MockProvider(fail_with="TIMEOUT")
    b.name = "b"

    router = ProviderRouter([a, b], mode=RoutingMode.AUTO)
    with pytest.raises(NoProviderAvailableError):
        router.complete([Message(role="user", content="hi")])

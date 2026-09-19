"""Model router: picks a provider and fails over on transient errors.

This is deliberately a thin slice of the full spec (MANUAL + AUTO only;
SMART routing based on cost/latency/task-complexity is not implemented
here). What IS implemented is real: capability checks, failover on
retryable errors, and refusal to fail over on auth/config errors.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field

from .providers.base import (
    AgentResponse,
    AIProvider,
    ErrorType,
    Message,
    ProviderError,
    RETRYABLE_ERROR_TYPES,
    ToolDefinition,
)


class RoutingMode(str, enum.Enum):
    MANUAL = "manual"  # use exactly the provider the caller names, never fail over
    AUTO = "auto"  # use the configured priority order, fail over on transient errors


@dataclass
class FailoverEvent:
    from_provider: str
    to_provider: str | None
    error_type: str
    message: str


@dataclass
class RouterResult:
    response: AgentResponse
    provider_used: str
    failover_events: list[FailoverEvent] = field(default_factory=list)


class NoProviderAvailableError(Exception):
    """Raised when every configured provider failed or none is configured."""


class ProviderRouter:
    def __init__(self, providers: list[AIProvider], mode: RoutingMode = RoutingMode.AUTO):
        if not providers:
            raise ValueError("ProviderRouter needs at least one provider")
        self.providers = providers
        self.mode = mode

    def _required_capabilities(self, tools: list[ToolDefinition] | None) -> list[str]:
        return ["tool_calling"] if tools else []

    def complete(
        self,
        messages: list[Message],
        system: str | None = None,
        tools: list[ToolDefinition] | None = None,
        provider_name: str | None = None,
    ) -> RouterResult:
        required = self._required_capabilities(tools)
        failover_events: list[FailoverEvent] = []

        if self.mode == RoutingMode.MANUAL or provider_name:
            candidates = [p for p in self.providers if p.name == provider_name] or self.providers[:1]
        else:
            candidates = self.providers

        last_error: ProviderError | None = None

        for i, provider in enumerate(candidates):
            if not provider.is_configured():
                error = ProviderError(
                    ErrorType.MISSING_API_KEY,
                    f"{provider.name} is not configured (missing API key). Run: codefix config",
                    provider.name,
                )
                # A missing/invalid key is a configuration problem, not a
                # transient failure - surface it immediately rather than
                # silently trying every other provider (spec rule 9).
                # Exception: if there are other candidates in AUTO mode that
                # ARE configured, still give them a chance first.
                remaining_configured = any(p.is_configured() for p in candidates[i + 1 :])
                if self.mode == RoutingMode.MANUAL or not remaining_configured:
                    raise error
                last_error = error
                failover_events.append(
                    FailoverEvent(
                        from_provider=provider.name,
                        to_provider=candidates[i + 1].name if i + 1 < len(candidates) else None,
                        error_type=error.error_type.value,
                        message=str(error),
                    )
                )
                continue

            if required and not provider.capabilities.supports(*required):
                # Capability mismatch: skip silently to the next candidate,
                # this isn't a "failure" worth reporting as a failover.
                continue

            try:
                response = provider.complete(messages, system=system, tools=tools)
                return RouterResult(
                    response=response, provider_used=provider.name, failover_events=failover_events
                )
            except ProviderError as exc:
                last_error = exc
                is_last = i == len(candidates) - 1
                next_provider = candidates[i + 1].name if not is_last else None

                if self.mode == RoutingMode.MANUAL:
                    # Manual mode: the user picked this provider explicitly.
                    # Never silently substitute another one for them.
                    raise

                if exc.error_type not in RETRYABLE_ERROR_TYPES:
                    # Auth/config problems require user action, not failover
                    # (spec rule 9). Surface immediately.
                    raise

                failover_events.append(
                    FailoverEvent(
                        from_provider=provider.name,
                        to_provider=next_provider,
                        error_type=exc.error_type.value,
                        message=str(exc),
                    )
                )
                continue

        raise NoProviderAvailableError(
            f"No configured provider could complete the request. Last error: {last_error}"
        )

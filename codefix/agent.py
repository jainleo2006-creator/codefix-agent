"""Bounded agent loop tying router + tools + task-state together.

Still a slice of the full orchestrator (rule 12): no planner/coder/tester/
debugger/reviewer role split. What's new versus the first slice: the loop
now persists a real TaskState after every turn, so if it's interrupted
(process killed, all providers exhausted, an unhandled error) the exact
conversation can be resumed later with `task_id` rather than restarted.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .providers.base import Message
from .router import NoProviderAvailableError, ProviderError, ProviderRouter
from .state import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    STATUS_NEEDS_REVIEW,
    SerializedMessage,
    TaskState,
    TaskStateStore,
)
from .tools.base import Tool

DEFAULT_MAX_ITERATIONS = 8

# Tool names whose path/command argument we track into TaskState so a human
# (or a resumed run) can see what's already been looked at or changed,
# without re-deriving it from the raw message log.
_INSPECTION_TOOLS = {"read_file", "git_status", "git_diff"}
_MODIFICATION_TOOLS = {"write_file", "edit_file", "create_file", "delete_file"}
_TEST_TOOLS = {"run_command", "run_tests"}


@dataclass
class AgentRunResult:
    task_id: str
    status: str
    final_text: str
    turns: int
    tool_calls_made: list[str] = field(default_factory=list)
    provider_history: list[str] = field(default_factory=list)
    failover_events: list[str] = field(default_factory=list)


def _to_messages(serialized: list[SerializedMessage]) -> list[Message]:
    return [
        Message(role=m.role, content=m.content, tool_call_id=m.tool_call_id, name=m.name)
        for m in serialized
    ]


def _record_message(state: TaskState, msg: Message) -> None:
    state.messages.append(
        SerializedMessage(role=msg.role, content=msg.content, tool_call_id=msg.tool_call_id, name=msg.name)
    )


def run_agent(
    router: ProviderRouter,
    tools: list[Tool],
    user_prompt: str,
    system: str | None = None,
    provider_name: str | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    task_store: TaskStateStore | None = None,
    task_id: str | None = None,
    project: str = ".",
) -> AgentRunResult:
    tools_by_name = {t.name: t for t in tools}
    tool_defs = [t.definition() for t in tools]

    # --- resolve task state: resume an existing task, or start a new one ---
    state: TaskState | None = None
    if task_store is not None:
        if task_id:
            state = task_store.load(task_id)
            if state is None:
                raise ValueError(f"No such task: {task_id}")
        else:
            state = task_store.create(user_request=user_prompt, project=project)

    if state is not None and state.messages:
        messages = _to_messages(state.messages)
        start_turn = len(state.completed_steps) + 1
    else:
        messages = [Message(role="user", content=user_prompt)]
        if state is not None:
            _record_message(state, messages[0])
        start_turn = 1

    tool_calls_made: list[str] = list(state.pending_actions) if state else []
    provider_history: list[str] = list(state.provider_history) if state else []
    failover_events: list[str] = []

    def _persist(phase: str, status: str = STATUS_IN_PROGRESS) -> None:
        if state is None or task_store is None:
            return
        state.current_phase = phase
        state.status = status
        state.provider_history = provider_history
        state.pending_actions = tool_calls_made
        task_store.save(state)

    try:
        for turn in range(start_turn, start_turn + max_iterations):
            _persist(phase=f"awaiting_provider_turn_{turn}")

            result = router.complete(messages, system=system, tools=tool_defs, provider_name=provider_name)
            provider_history.append(result.provider_used)
            failover_events.extend(
                f"{e.from_provider} -> {e.to_provider or 'NONE'} ({e.error_type})"
                for e in result.failover_events
            )

            response = result.response

            if response.text:
                assistant_msg = Message(role="assistant", content=response.text)
                messages.append(assistant_msg)
                if state is not None:
                    _record_message(state, assistant_msg)

            if state is not None:
                state.completed_steps.append(f"turn {turn}: provider={result.provider_used}")

            if not response.tool_calls:
                _persist(phase="done", status=STATUS_COMPLETED)
                return AgentRunResult(
                    task_id=state.task_id if state else "",
                    status=STATUS_COMPLETED,
                    final_text=response.text,
                    turns=turn,
                    tool_calls_made=tool_calls_made,
                    provider_history=provider_history,
                    failover_events=failover_events,
                )

            for call in response.tool_calls:
                tool = tools_by_name.get(call.name)
                if tool is None:
                    tool_result_text = json.dumps({"error": f"Unknown tool: {call.name}"})
                else:
                    outcome = tool.execute(**call.arguments)
                    tool_calls_made.append(f"{call.name}({call.arguments})")
                    tool_result_text = outcome.output if outcome.success else f"ERROR: {outcome.error}"

                    if state is not None:
                        if call.name in _INSPECTION_TOOLS and "path" in call.arguments:
                            path = call.arguments["path"]
                            if path not in state.files_inspected:
                                state.files_inspected.append(path)
                        elif call.name in _MODIFICATION_TOOLS and "path" in call.arguments:
                            path = call.arguments["path"]
                            if path not in state.files_modified:
                                state.files_modified.append(path)
                        elif call.name in _TEST_TOOLS:
                            state.tests_run.append(call.arguments.get("command", call.name))
                            state.test_results[str(len(state.tests_run))] = (
                                "pass" if outcome.success else "fail"
                            )

                tool_msg = Message(role="tool", content=tool_result_text, tool_call_id=call.id, name=call.name)
                messages.append(tool_msg)
                if state is not None:
                    _record_message(state, tool_msg)

            _persist(phase=f"turn_{turn}_tools_executed")

        _persist(phase="max_iterations_reached", status=STATUS_NEEDS_REVIEW)
        return AgentRunResult(
            task_id=state.task_id if state else "",
            status=STATUS_NEEDS_REVIEW,
            final_text=f"[stopped after {max_iterations} iterations without a final answer]",
            turns=start_turn + max_iterations - 1,
            tool_calls_made=tool_calls_made,
            provider_history=provider_history,
            failover_events=failover_events,
        )

    except (ProviderError, NoProviderAvailableError):
        # Task state is already persisted up to the last successful turn
        # (see _persist calls above) - the caller can resume with task_id
        # once the underlying provider problem is fixed.
        _persist(phase="provider_failure", status=STATUS_FAILED)
        raise

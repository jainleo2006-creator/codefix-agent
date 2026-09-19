import pytest

from codefix.agent import run_agent
from codefix.providers.base import ProviderError
from codefix.providers.mock_provider import MockProvider
from codefix.router import ProviderRouter, RoutingMode
from codefix.state import STATUS_COMPLETED, STATUS_FAILED, TaskStateStore
from codefix.tools.file_tools import ReadFileTool


def _project_with_file(tmp_path):
    (tmp_path / "app.py").write_text("def add(a, b): return a - b  # bug")
    return tmp_path


def test_successful_run_persists_completed_state(tmp_path):
    project = _project_with_file(tmp_path)
    store = TaskStateStore(project / ".codefix" / "codefix.db")
    router = ProviderRouter([MockProvider()], mode=RoutingMode.MANUAL)
    tools = [ReadFileTool(project)]

    result = run_agent(
        router, tools, user_prompt="check app.py", task_store=store, project=str(project)
    )

    assert result.status == STATUS_COMPLETED
    loaded = store.load(result.task_id)
    assert loaded.status == STATUS_COMPLETED
    assert loaded.files_inspected == ["app.py"]
    assert len(loaded.messages) > 0


def test_failed_run_persists_state_for_resume(tmp_path):
    project = _project_with_file(tmp_path)
    store = TaskStateStore(project / ".codefix" / "codefix.db")
    failing_provider = MockProvider(fail_with="RATE_LIMITED")
    router = ProviderRouter([failing_provider], mode=RoutingMode.MANUAL)
    tools = [ReadFileTool(project)]

    with pytest.raises(ProviderError):
        run_agent(router, tools, user_prompt="check app.py", task_store=store, project=str(project))

    tasks = store.list(project=str(project))
    assert len(tasks) == 1
    assert tasks[0].status == STATUS_FAILED
    # The original user message must have been persisted before the failure.
    assert tasks[0].messages[0].content == "check app.py"


def test_resume_continues_with_healthy_provider(tmp_path):
    project = _project_with_file(tmp_path)
    store = TaskStateStore(project / ".codefix" / "codefix.db")
    tools = [ReadFileTool(project)]

    failing_provider = MockProvider(fail_with="RATE_LIMITED")
    failing_router = ProviderRouter([failing_provider], mode=RoutingMode.MANUAL)
    with pytest.raises(ProviderError):
        run_agent(
            failing_router, tools, user_prompt="check app.py", task_store=store, project=str(project)
        )

    task_id = store.list(project=str(project))[0].task_id

    healthy_router = ProviderRouter([MockProvider()], mode=RoutingMode.MANUAL)
    result = run_agent(
        healthy_router,
        tools,
        user_prompt="check app.py",
        task_store=store,
        task_id=task_id,
        project=str(project),
    )

    assert result.status == STATUS_COMPLETED
    assert result.task_id == task_id


def test_resume_unknown_task_id_raises(tmp_path):
    project = _project_with_file(tmp_path)
    store = TaskStateStore(project / ".codefix" / "codefix.db")
    router = ProviderRouter([MockProvider()], mode=RoutingMode.MANUAL)
    tools = [ReadFileTool(project)]

    with pytest.raises(ValueError):
        run_agent(
            router,
            tools,
            user_prompt="check app.py",
            task_store=store,
            task_id="nonexistent",
            project=str(project),
        )

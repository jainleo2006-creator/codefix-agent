from codefix.state import STATUS_COMPLETED, TaskState, TaskStateStore


def test_create_and_load_roundtrip(tmp_path):
    store = TaskStateStore(tmp_path / "codefix.db")
    state = store.create(user_request="fix the bug", project="myproj")
    state.files_inspected.append("app.py")
    state.completed_steps.append("turn 1")
    store.save(state)

    loaded = store.load(state.task_id)
    assert loaded is not None
    assert loaded.user_request == "fix the bug"
    assert loaded.files_inspected == ["app.py"]
    assert loaded.completed_steps == ["turn 1"]


def test_load_missing_task_returns_none(tmp_path):
    store = TaskStateStore(tmp_path / "codefix.db")
    assert store.load("does-not-exist") is None


def test_list_filters_by_project_and_orders_by_recency(tmp_path):
    store = TaskStateStore(tmp_path / "codefix.db")
    a = store.create(user_request="task a", project="proj1")
    store.create(user_request="task b", project="proj2")
    a.status = STATUS_COMPLETED
    store.save(a)

    only_proj1 = store.list(project="proj1")
    assert len(only_proj1) == 1
    assert only_proj1[0].task_id == a.task_id
    assert only_proj1[0].status == STATUS_COMPLETED


def test_messages_and_checkpoints_survive_roundtrip(tmp_path):
    from codefix.state import Checkpoint, SerializedMessage

    store = TaskStateStore(tmp_path / "codefix.db")
    state = store.create(user_request="fix it", project="proj")
    state.messages.append(SerializedMessage(role="user", content="hello"))
    state.checkpoints.append(Checkpoint(label="before fix", commit_hash="abc123"))
    store.save(state)

    loaded = store.load(state.task_id)
    assert loaded.messages[0].content == "hello"
    assert loaded.checkpoints[0].commit_hash == "abc123"

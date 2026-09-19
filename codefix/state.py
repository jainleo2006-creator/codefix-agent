"""Persistent task-state engine (spec section 10).

Backed by real SQLite (rule 57: SQLite by default), stored at
<project_root>/.codefix/codefix.db. This is what lets a failover actually
resume a task instead of restarting it: the exact conversation state is
serialized after every turn, so if a provider dies mid-task the next
provider (or the same one on retry) picks up from that point rather than
from scratch.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_NEEDS_REVIEW = "needs_review"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class SerializedMessage:
    role: str
    content: str
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class Checkpoint:
    label: str
    commit_hash: str
    created_at: str = field(default_factory=_now)


@dataclass
class TaskState:
    task_id: str
    user_request: str
    project: str
    current_phase: str = "planning"
    status: str = STATUS_IN_PROGRESS
    completed_steps: list[str] = field(default_factory=list)
    files_inspected: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    tests_run: list[str] = field(default_factory=list)
    test_results: dict[str, Any] = field(default_factory=dict)
    current_errors: list[str] = field(default_factory=list)
    current_hypothesis: str | None = None
    pending_actions: list[str] = field(default_factory=list)
    provider_history: list[str] = field(default_factory=list)
    checkpoints: list[Checkpoint] = field(default_factory=list)
    # Not in the original spec table, but required for real resume: the
    # exact message history so the next provider sees precisely what the
    # previous one saw, not a lossy summary.
    messages: list[SerializedMessage] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "TaskState":
        data = json.loads(raw)
        data["checkpoints"] = [Checkpoint(**c) for c in data.get("checkpoints", [])]
        data["messages"] = [SerializedMessage(**m) for m in data.get("messages", [])]
        return cls(**data)


class TaskStateStore:
    """SQLite-backed store. One row per task, state serialized as JSON.

    A single JSON blob column (rather than one column per field) is a
    deliberate simplification for this slice: the schema in the full spec
    will evolve, and a blob avoids a migration for every new field while
    still giving real persistence, real concurrency safety (SQLite handles
    that), and real queryability via task_id/status/updated_at.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    project TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    data TEXT NOT NULL
                )
                """
            )

    def create(self, user_request: str, project: str) -> TaskState:
        state = TaskState(task_id=str(uuid.uuid4())[:8], user_request=user_request, project=str(project))
        self.save(state)
        return state

    def save(self, state: TaskState) -> None:
        state.updated_at = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO tasks (task_id, status, project, updated_at, data)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status=excluded.status,
                    project=excluded.project,
                    updated_at=excluded.updated_at,
                    data=excluded.data
                """,
                (state.task_id, state.status, state.project, state.updated_at, state.to_json()),
            )

    def load(self, task_id: str) -> TaskState | None:
        with self._connect() as conn:
            row = conn.execute("SELECT data FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return TaskState.from_json(row[0]) if row else None

    def list(self, project: str | None = None) -> list[TaskState]:
        query = "SELECT data FROM tasks"
        params: tuple = ()
        if project is not None:
            query += " WHERE project = ?"
            params = (project,)
        query += " ORDER BY updated_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [TaskState.from_json(r[0]) for r in rows]

    def delete(self, task_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))

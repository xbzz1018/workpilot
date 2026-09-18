"""Small SQLite WAL task/event store for the private single-node service."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from workpilot.schemas import ReportType, RunStatus, TaskEvent, TaskRecord, WorkflowStage


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TaskStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    report_type TEXT NOT NULL,
                    period TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    error TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE NOT NULL,
                    task_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES tasks(task_id)
                );
                CREATE INDEX IF NOT EXISTS events_task_sequence ON events(task_id, sequence);
            """)

    def create(self, task_id: str, report_type: ReportType, period: str) -> TaskRecord:
        now = _now()
        record = TaskRecord(task_id=task_id, report_type=report_type, period=period, status=RunStatus.QUEUED, stage=WorkflowStage.QUEUED, created_at=now, updated_at=now)
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, record.report_type.value, period, record.status.value, record.stage.value, now.isoformat(), now.isoformat(), None, 0),
            )
        self.add_event(task_id, WorkflowStage.QUEUED, "Task queued")
        return record

    def get(self, task_id: str) -> TaskRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return self._record(row) if row else None

    def list(self, limit: int = 100) -> list[TaskRecord]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._record(row) for row in rows]

    def update(self, task_id: str, *, status: RunStatus | None = None, stage: WorkflowStage | None = None, error: str | None = None) -> TaskRecord:
        current = self.get(task_id)
        if current is None:
            raise KeyError(task_id)
        updated = current.model_copy(update={
            "status": status or current.status,
            "stage": stage or current.stage,
            "error": error,
            "updated_at": _now(),
        })
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE tasks SET status=?, stage=?, updated_at=?, error=? WHERE task_id=?",
                (updated.status.value, updated.stage.value, updated.updated_at.isoformat(), updated.error, task_id),
            )
        return updated

    def request_cancel(self, task_id: str) -> None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute("UPDATE tasks SET cancel_requested=1, updated_at=? WHERE task_id=?", (_now().isoformat(), task_id))
            if cursor.rowcount == 0:
                raise KeyError(task_id)

    def clear_cancel(self, task_id: str) -> None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute("UPDATE tasks SET cancel_requested=0, updated_at=? WHERE task_id=?", (_now().isoformat(), task_id))
            if cursor.rowcount == 0:
                raise KeyError(task_id)

    def is_cancelled(self, task_id: str) -> bool:
        record = self.get(task_id)
        return bool(record and record.cancel_requested)

    def add_event(self, task_id: str, stage: WorkflowStage, message: str, data: dict | None = None) -> TaskEvent:
        event = TaskEvent(event_id=uuid.uuid4().hex, task_id=task_id, stage=stage, message=message, data=data or {})
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO events(event_id,task_id,stage,message,created_at,data) VALUES(?,?,?,?,?,?)",
                (event.event_id, task_id, stage.value, message, event.created_at.isoformat(), json.dumps(event.data, ensure_ascii=False)),
            )
        return event

    def events(self, task_id: str, after: int = 0) -> list[tuple[int, TaskEvent]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM events WHERE task_id=? AND sequence>? ORDER BY sequence", (task_id, after)).fetchall()
        return [(row["sequence"], TaskEvent(event_id=row["event_id"], task_id=row["task_id"], stage=row["stage"], message=row["message"], created_at=row["created_at"], data=json.loads(row["data"]))) for row in rows]

    def delete(self, task_id: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM events WHERE task_id=?", (task_id,))
            connection.execute("DELETE FROM tasks WHERE task_id=?", (task_id,))

    @staticmethod
    def _record(row: sqlite3.Row) -> TaskRecord:
        return TaskRecord(
            task_id=row["task_id"], report_type=row["report_type"], period=row["period"],
            status=row["status"], stage=row["stage"], created_at=row["created_at"], updated_at=row["updated_at"],
            error=row["error"], cancel_requested=bool(row["cancel_requested"]),
        )

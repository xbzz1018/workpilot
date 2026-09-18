from __future__ import annotations

from workpilot.schemas import RunStatus, WorkflowStage
from workpilot.task_store import TaskStore


def test_task_store_lifecycle_and_events(tmp_path) -> None:
    store = TaskStore(tmp_path / "tasks.sqlite")
    created = store.create("t1", "work_summary", "2026-W35")
    assert created.status is RunStatus.QUEUED
    assert store.get("missing") is None
    assert store.list() == [created]
    updated = store.update("t1", status=RunStatus.EXTRACTING, stage=WorkflowStage.EXTRACTING)
    assert updated.stage is WorkflowStage.EXTRACTING
    event = store.add_event("t1", WorkflowStage.EXTRACTING, "started", {"x": 1})
    events = store.events("t1")
    assert events[-1][1] == event
    assert store.events("t1", after=events[-1][0]) == []
    store.request_cancel("t1")
    assert store.is_cancelled("t1")
    store.clear_cancel("t1")
    assert not store.is_cancelled("t1")
    assert not store.is_cancelled("missing")
    with __import__("pytest").raises(KeyError):
        store.update("missing", status=RunStatus.FAILED)
    with __import__("pytest").raises(KeyError):
        store.request_cancel("missing")
    with __import__("pytest").raises(KeyError):
        store.clear_cancel("missing")
    store.delete("t1")
    assert store.get("t1") is None

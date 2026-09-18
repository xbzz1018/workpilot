from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from workpilot.schemas import Fact, TaskSpec
from workpilot.workspace import TaskWorkspace, WorkspaceError


def test_task_and_fact_validation() -> None:
    with pytest.raises(ValidationError):
        TaskSpec(task_id="../escape", report_type="work_summary", period="p")
    with pytest.raises(ValidationError, match="unique"):
        Fact(fact_id="fact_a", statement="x", evidence_ids=["ev_a", "ev_a"])


def test_workspace_atomic_json_and_escape(tmp_path: Path, task: TaskSpec) -> None:
    workspace = TaskWorkspace(tmp_path, task.task_id)
    workspace.write_task(task)
    assert workspace.read_json("task.json")["task_id"] == task.task_id
    with pytest.raises(WorkspaceError):
        workspace.write_text("../outside.txt", "no")
    assert not (tmp_path / "outside.txt").exists()


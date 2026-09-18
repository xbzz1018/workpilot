"""Filesystem layout and atomic artifact persistence for a WorkPilot task."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from workpilot.schemas import ImportedPackage, TaskSpec


class WorkspaceError(ValueError):
    pass


class TaskWorkspace:
    def __init__(self, root: str | Path, task_id: str) -> None:
        self.root = Path(root).expanduser().resolve()
        self.task_id = task_id
        self.path = (self.root / task_id).resolve()
        try:
            self.path.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceError("task workspace escapes configured root") from exc
        self.input_dir = self.path / "input"
        self.intermediate_dir = self.path / "intermediate"
        self.drafts_dir = self.path / "drafts"
        self.final_dir = self.path / "final"
        self.db_path = self.path / "checkpoint.sqlite"

    def initialize(self) -> None:
        for directory in (self.input_dir, self.intermediate_dir, self.drafts_dir, self.final_dir):
            directory.mkdir(parents=True, exist_ok=True)

    def stage_input(self, input_root: str | Path, package: ImportedPackage) -> None:
        self.initialize()
        source_root = Path(input_root).expanduser().resolve(strict=True)
        for material in package.materials:
            source = (source_root / material.source_path).resolve(strict=True)
            destination = self.input_dir / material.source_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        self.write_json("intermediate/materials.json", package)

    def write_task(self, task: TaskSpec) -> None:
        self.initialize()
        self.write_json("task.json", task)

    def write_json(self, relative_path: str, value: BaseModel | dict[str, Any] | list[Any]) -> Path:
        target = self._resolve(relative_path)
        payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
        return self._atomic_write(target, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

    def write_text(self, relative_path: str, value: str) -> Path:
        return self._atomic_write(self._resolve(relative_path), value)

    def read_json(self, relative_path: str) -> Any:
        return json.loads(self._resolve(relative_path).read_text(encoding="utf-8"))

    def exists(self, relative_path: str) -> bool:
        return self._resolve(relative_path).exists()

    def _resolve(self, relative_path: str) -> Path:
        target = (self.path / relative_path).resolve()
        try:
            target.relative_to(self.path)
        except ValueError as exc:
            raise WorkspaceError("artifact path escapes task workspace") from exc
        return target

    @staticmethod
    def _atomic_write(target: Path, value: str) -> Path:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent, text=True)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
                stream.write(value)
            os.replace(temp_name, target)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
        return target


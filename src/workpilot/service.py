"""Application service shared by FastAPI and future CLI commands."""

from __future__ import annotations

import shutil
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from workpilot.ingestion import MAX_PACKAGE_BYTES, SUPPORTED_TYPES, import_package
from workpilot.pipeline import PipelineCancelled, approve_pipeline, run_pipeline
from workpilot.schemas import ImportedPackage, ReportType, RunStatus, TaskRecord, TaskSpec, WorkflowStage
from workpilot.settings import Settings
from workpilot.task_store import TaskStore
from workpilot.workspace import TaskWorkspace


class ApplicationService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.store = TaskStore(settings.task_db_path)
        self.executor = ThreadPoolExecutor(max_workers=settings.max_active_tasks, thread_name_prefix="workpilot")
        self._futures: dict[str, Future] = {}
        self._lock = threading.Lock()
        self._evaluations: dict[str, dict] = {}

    def submit(self, report_type: ReportType, period: str, files: list[tuple[str, bytes]]) -> TaskRecord:
        if not files:
            raise ValueError("at least one input file is required")
        if sum(len(content) for _, content in files) > MAX_PACKAGE_BYTES:
            raise ValueError("work package exceeds 10 MiB limit")
        task_id = uuid.uuid4().hex[:16]
        workspace = TaskWorkspace(self.settings.workspace_root, task_id)
        incoming = workspace.path / "incoming"
        incoming.mkdir(parents=True, exist_ok=False)
        seen: set[str] = set()
        try:
            for name, content in files:
                safe_name = Path(name).name
                if safe_name != name or Path(safe_name).suffix.lower() not in SUPPORTED_TYPES:
                    raise ValueError(f"unsafe or unsupported filename: {name}")
                if safe_name.casefold() in seen:
                    raise ValueError(f"duplicate filename: {safe_name}")
                seen.add(safe_name.casefold())
                (incoming / safe_name).write_bytes(content)
        except Exception:
            shutil.rmtree(workspace.path, ignore_errors=True)
            raise
        record = self.store.create(task_id, report_type, period)
        self._schedule(task_id)
        return record

    def _schedule(self, task_id: str) -> None:
        with self._lock:
            future = self.executor.submit(self._execute, task_id)
            self._futures[task_id] = future
            future.add_done_callback(lambda _: self._futures.pop(task_id, None))

    def _execute(self, task_id: str) -> None:
        record = self.store.get(task_id)
        if record is None:
            return
        workspace = TaskWorkspace(self.settings.workspace_root, task_id)
        try:
            task = TaskSpec(task_id=task_id, report_type=record.report_type, period=record.period)
            if not workspace.exists("task.json"):
                self.store.update(task_id, status=RunStatus.INGESTING, stage=WorkflowStage.INGESTING)
                self.store.add_event(task_id, WorkflowStage.INGESTING, "Importing work materials")
                package = import_package(workspace.path / "incoming")
                workspace.write_task(task)
                workspace.stage_input(workspace.path / "incoming", package)
            else:
                package = ImportedPackage.model_validate(workspace.read_json("intermediate/materials.json"))

            def on_stage(stage: WorkflowStage, message: str) -> None:
                status = RunStatus(stage.value) if stage.value in RunStatus._value2member_map_ else record.status
                self.store.update(task_id, status=status, stage=stage)
                self.store.add_event(task_id, stage, message)

            run_pipeline(
                workspace, task, package, settings=self.settings, on_stage=on_stage,
                is_cancelled=lambda: self.store.is_cancelled(task_id),
            )
            self.store.update(task_id, status=RunStatus.WAITING_APPROVAL, stage=WorkflowStage.AWAITING_APPROVAL)
        except PipelineCancelled:
            self.store.update(task_id, status=RunStatus.CANCELLED, stage=WorkflowStage.CANCELLED)
            self.store.add_event(task_id, WorkflowStage.CANCELLED, "Task cancelled")
        except Exception as exc:
            self.store.update(task_id, status=RunStatus.FAILED, stage=WorkflowStage.FAILED, error=f"{type(exc).__name__}: {exc}")
            self.store.add_event(task_id, WorkflowStage.FAILED, "Task failed", {"error_type": type(exc).__name__})

    def decide(self, task_id: str, decision: str, edited_markdown: str | None = None) -> TaskRecord:
        record = self.require(task_id)
        if record.status is not RunStatus.WAITING_APPROVAL:
            raise ValueError("task is not awaiting approval")
        workspace = TaskWorkspace(self.settings.workspace_root, task_id)
        report = approve_pipeline(workspace, decision, edited_markdown)
        if report is None:
            updated = self.store.update(task_id, status=RunStatus.REJECTED, stage=WorkflowStage.REJECTED)
            self.store.add_event(task_id, WorkflowStage.REJECTED, "Draft rejected")
        else:
            updated = self.store.update(task_id, status=RunStatus.COMPLETED, stage=WorkflowStage.COMPLETED)
            self.store.add_event(task_id, WorkflowStage.COMPLETED, "Final report exported")
        return updated

    def cancel(self, task_id: str) -> TaskRecord:
        record = self.require(task_id)
        if record.status in {RunStatus.COMPLETED, RunStatus.REJECTED, RunStatus.CANCELLED}:
            raise ValueError("terminal task cannot be cancelled")
        self.store.request_cancel(task_id)
        if record.status is RunStatus.WAITING_APPROVAL:
            updated = self.store.update(task_id, status=RunStatus.CANCELLED, stage=WorkflowStage.CANCELLED)
            self.store.add_event(task_id, WorkflowStage.CANCELLED, "Waiting draft cancelled")
            return updated
        return self.require(task_id)

    def retry(self, task_id: str) -> TaskRecord:
        record = self.require(task_id)
        if record.status is not RunStatus.FAILED:
            raise ValueError("only failed tasks can be retried")
        self.store.clear_cancel(task_id)
        updated = self.store.update(task_id, status=RunStatus.RECOVERING, stage=WorkflowStage.RECOVERING)
        self.store.add_event(task_id, WorkflowStage.RECOVERING, "Recovering from persisted stage")
        self._schedule(task_id)
        return updated

    def require(self, task_id: str) -> TaskRecord:
        record = self.store.get(task_id)
        if record is None:
            raise KeyError(task_id)
        return record

    def recover_incomplete(self) -> None:
        terminal = {RunStatus.COMPLETED, RunStatus.REJECTED, RunStatus.CANCELLED, RunStatus.WAITING_APPROVAL}
        for record in self.store.list(1000):
            if record.status not in terminal:
                self.store.update(record.task_id, status=RunStatus.RECOVERING, stage=WorkflowStage.RECOVERING)
                self._schedule(record.task_id)

    def cleanup_expired(self, now: datetime | None = None) -> list[str]:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=self.settings.retention_days)
        removed: list[str] = []
        for record in self.store.list(1000):
            if record.updated_at < cutoff and record.status in {RunStatus.COMPLETED, RunStatus.REJECTED, RunStatus.CANCELLED, RunStatus.FAILED}:
                path = TaskWorkspace(self.settings.workspace_root, record.task_id).path
                if path.exists():
                    shutil.rmtree(path)
                self.store.delete(record.task_id)
                removed.append(record.task_id)
        return removed

    def submit_evaluation(self, split: str, system: str) -> dict:
        evaluation_id = uuid.uuid4().hex[:16]
        record = {"evaluation_id": evaluation_id, "split": split, "system": system, "status": "queued", "output": None, "error": None}
        with self._lock:
            self._evaluations[evaluation_id] = record
        self.executor.submit(self._execute_evaluation, evaluation_id)
        return dict(record)

    def _execute_evaluation(self, evaluation_id: str) -> None:
        from workpilot.evaluation import run_evaluation

        with self._lock:
            self._evaluations[evaluation_id]["status"] = "running"
            record = dict(self._evaluations[evaluation_id])
        try:
            output = run_evaluation(
                Path(__file__).resolve().parents[2] / "evals-v2",
                Path(__file__).resolve().parents[2] / "results",
                record["split"],
                record["system"],
            )
            with self._lock:
                self._evaluations[evaluation_id].update(status="completed", output=str(output))
        except Exception as exc:
            with self._lock:
                self._evaluations[evaluation_id].update(status="failed", error=f"{type(exc).__name__}: {exc}")

    def evaluation(self, evaluation_id: str) -> dict:
        with self._lock:
            if evaluation_id not in self._evaluations:
                raise KeyError(evaluation_id)
            return dict(self._evaluations[evaluation_id])

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False, cancel_futures=False)

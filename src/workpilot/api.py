"""FastAPI surface for the private WorkPilot service."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

from workpilot import __version__
from workpilot.schemas import ImportedPackage, ReportBundle, ReportType, RunStatus, TaskRecord, VerificationResult
from workpilot.service import ApplicationService
from workpilot.settings import Settings
from workpilot.workspace import TaskWorkspace


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "edit", "reject"]
    edited_markdown: str | None = None


class ErrorResponse(BaseModel):
    code: str
    message: str


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    split: Literal["dev", "test", "all"]
    system: Literal["baseline", "workpilot_single", "workpilot_heterogeneous", "challenger", "both", "all_systems", "dev_matrix", "test_matrix"]


def create_app(settings: Settings | None = None, service: ApplicationService | None = None) -> FastAPI:
    settings = settings or Settings.from_env(allow_placeholder=True)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service = service or ApplicationService(settings)
        app.state.service.cleanup_expired()
        app.state.service.recover_incomplete()

        async def cleanup_loop() -> None:
            while True:
                await asyncio.sleep(24 * 60 * 60)
                await asyncio.to_thread(app.state.service.cleanup_expired)

        cleanup_task = asyncio.create_task(cleanup_loop())
        try:
            yield
        finally:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task
            app.state.service.shutdown()

    app = FastAPI(
        title="WorkPilot API",
        version=__version__,
        summary="Evidence-grounded work summary agent",
        openapi_version="3.1.0",
        lifespan=lifespan,
    )

    def svc(request: Request) -> ApplicationService:
        return request.app.state.service

    def require(request: Request, task_id: str) -> TaskRecord:
        try:
            return svc(request).require(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"code": "task_not_found", "message": task_id}) from exc

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/system/models")
    def model_config() -> dict[str, str]:
        return {
            "main": settings.main.model,
            "extractor": settings.extractor.model,
            "verifier": settings.verifier.model,
            "baseline": settings.baseline.model,
            "challenger": settings.challenger.model,
        }

    @app.post("/api/v1/tasks", response_model=TaskRecord, status_code=status.HTTP_202_ACCEPTED)
    async def create_task(
        request: Request,
        report_type: Annotated[ReportType, Form()],
        period: Annotated[str, Form(min_length=1, max_length=120)],
        files: Annotated[list[UploadFile], File()],
    ) -> TaskRecord:
        payload: list[tuple[str, bytes]] = []
        for upload in files:
            payload.append((upload.filename or "", await upload.read()))
        try:
            return svc(request).submit(report_type, period, payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={"code": "invalid_input", "message": str(exc)}) from exc

    @app.get("/api/v1/tasks", response_model=list[TaskRecord])
    def list_tasks(request: Request, limit: int = 100) -> list[TaskRecord]:
        return svc(request).store.list(min(max(limit, 1), 500))

    @app.get("/api/v1/tasks/{task_id}", response_model=TaskRecord)
    def get_task(request: Request, task_id: str) -> TaskRecord:
        return require(request, task_id)

    @app.get("/api/v1/tasks/{task_id}/events")
    async def task_events(request: Request, task_id: str) -> StreamingResponse:
        require(request, task_id)

        async def stream():
            cursor = 0
            terminal = {RunStatus.COMPLETED, RunStatus.REJECTED, RunStatus.FAILED, RunStatus.CANCELLED}
            while True:
                for sequence, event in svc(request).store.events(task_id, cursor):
                    cursor = sequence
                    yield f"id: {sequence}\nevent: stage\ndata: {event.model_dump_json()}\n\n"
                record = svc(request).require(task_id)
                if record.status in terminal or await request.is_disconnected():
                    break
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/v1/tasks/{task_id}/materials", response_model=ImportedPackage)
    def materials(request: Request, task_id: str) -> ImportedPackage:
        require(request, task_id)
        workspace = TaskWorkspace(settings.workspace_root, task_id)
        return ImportedPackage.model_validate(workspace.read_json("intermediate/materials.json"))

    @app.get("/api/v1/tasks/{task_id}/facts", response_model=VerificationResult)
    def facts(request: Request, task_id: str) -> VerificationResult:
        require(request, task_id)
        workspace = TaskWorkspace(settings.workspace_root, task_id)
        if not workspace.exists("intermediate/verification-v2.json"):
            raise HTTPException(status_code=409, detail={"code": "facts_not_ready", "message": "verification has not completed"})
        return VerificationResult.model_validate(workspace.read_json("intermediate/verification-v2.json"))

    @app.get("/api/v1/tasks/{task_id}/draft", response_model=ReportBundle)
    def draft(request: Request, task_id: str) -> ReportBundle:
        require(request, task_id)
        workspace = TaskWorkspace(settings.workspace_root, task_id)
        if not workspace.exists("drafts/report-v2.json"):
            raise HTTPException(status_code=409, detail={"code": "draft_not_ready", "message": "draft has not completed"})
        return ReportBundle.model_validate(workspace.read_json("drafts/report-v2.json"))

    @app.post("/api/v1/tasks/{task_id}/decisions", response_model=TaskRecord)
    def decide(request: Request, task_id: str, body: DecisionRequest) -> TaskRecord:
        require(request, task_id)
        try:
            return svc(request).decide(task_id, body.decision, body.edited_markdown)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": "invalid_state", "message": str(exc)}) from exc

    @app.post("/api/v1/tasks/{task_id}/cancel", response_model=TaskRecord)
    def cancel(request: Request, task_id: str) -> TaskRecord:
        require(request, task_id)
        try:
            return svc(request).cancel(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": "invalid_state", "message": str(exc)}) from exc

    @app.post("/api/v1/tasks/{task_id}/retry", response_model=TaskRecord, status_code=202)
    def retry(request: Request, task_id: str) -> TaskRecord:
        require(request, task_id)
        try:
            return svc(request).retry(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail={"code": "invalid_state", "message": str(exc)}) from exc

    @app.get("/api/v1/tasks/{task_id}/history")
    def history(request: Request, task_id: str) -> list[dict]:
        require(request, task_id)
        return [{"sequence": sequence, **event.model_dump(mode="json")} for sequence, event in svc(request).store.events(task_id)]

    @app.get("/api/v1/tasks/{task_id}/usage")
    def usage(request: Request, task_id: str) -> list[dict]:
        require(request, task_id)
        workspace = TaskWorkspace(settings.workspace_root, task_id)
        return workspace.read_json("usage.json") if workspace.exists("usage.json") else []

    @app.get("/api/v1/tasks/{task_id}/artifacts/{name}")
    def artifact(request: Request, task_id: str, name: str) -> FileResponse:
        require(request, task_id)
        relative = {"report.md": "final/report.md", "report.json": "final/report.json", "draft.md": "drafts/report-v2.md", "draft.json": "drafts/report-v2.json"}.get(name)
        if relative is None:
            raise HTTPException(status_code=404, detail={"code": "artifact_not_found", "message": name})
        path = TaskWorkspace(settings.workspace_root, task_id)._resolve(relative)
        if not path.exists():
            raise HTTPException(status_code=409, detail={"code": "artifact_not_ready", "message": name})
        return FileResponse(path, filename=name)

    @app.post("/api/v1/evaluations", status_code=202)
    def create_evaluation(request: Request, body: EvaluationRequest) -> dict:
        return svc(request).submit_evaluation(body.split, body.system)

    @app.get("/api/v1/evaluations/{evaluation_id}")
    def get_evaluation(request: Request, evaluation_id: str) -> dict:
        try:
            return svc(request).evaluation(evaluation_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"code": "evaluation_not_found", "message": evaluation_id}) from exc

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("workpilot.api:app", host="127.0.0.1", port=8000, reload=False)

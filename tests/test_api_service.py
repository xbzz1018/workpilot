from __future__ import annotations

import hashlib
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from workpilot.api import create_app
from workpilot.renderer import render_report
from workpilot.schemas import Fact, ReportClaim, ReportDraft, RunStatus, VerificationResult, WorkflowStage
from workpilot.service import ApplicationService
from workpilot.settings import Settings


def settings(tmp_path, monkeypatch) -> Settings:
    values = {
        "WORKPILOT_MAIN_API_KEY": "main-test",
        "WORKPILOT_EXTRACTOR_API_KEY": "extract-test",
        "WORKPILOT_VERIFIER_API_KEY": "verify-test",
        "WORKPILOT_CHALLENGER_API_KEY": "challenge-test",
        "WORKPILOT_WORKSPACE": str(tmp_path / "workspace"),
        "WORKPILOT_TASK_DB": str(tmp_path / "workspace" / "tasks.sqlite"),
        "WORKPILOT_RETENTION_DAYS": "30",
        "WORKPILOT_MAX_ACTIVE_TASKS": "3",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return Settings.from_env()


def fake_pipeline(workspace, task, package, *, settings, models=None, on_stage=None, is_cancelled=None):
    for stage in (WorkflowStage.EXTRACTING, WorkflowStage.VALIDATING, WorkflowStage.VERIFYING, WorkflowStage.DRAFTING):
        if on_stage:
            on_stage(stage, stage.value)
    evidence = package.evidence[0]
    fact = Fact(
        fact_id=f"fact_{evidence.evidence_id.removeprefix('ev_')}",
        subject="Atlas 发布", predicate="完成", statement=evidence.excerpt,
        evidence_ids=[evidence.evidence_id], period=task.period,
    )
    verification = VerificationResult(facts=[fact])
    workspace.write_json("intermediate/verification-v2.json", verification)
    claim = ReportClaim(
        claim_id="claim_api", section="核心成果", text=fact.statement,
        fact_ids=[fact.fact_id], evidence_ids=fact.evidence_ids,
    )
    report = render_report(task, ReportDraft(claims=[claim]), [fact], package.evidence, [], [], settings.main.model)
    workspace.write_json("drafts/report-v2.json", report)
    workspace.write_text("drafts/report-v2.md", report.markdown)
    workspace.write_json("usage.json", [])
    return report


def wait_for(client: TestClient, task_id: str, wanted: str = "waiting_approval") -> dict:
    for _ in range(100):
        data = client.get(f"/api/v1/tasks/{task_id}").json()
        if data["status"] == wanted:
            return data
        time.sleep(0.02)
    raise AssertionError(data)


@pytest.fixture
def api(tmp_path, monkeypatch):
    config = settings(tmp_path, monkeypatch)
    monkeypatch.setattr("workpilot.service.run_pipeline", fake_pipeline)
    service = ApplicationService(config)
    with TestClient(create_app(config, service)) as client:
        yield client, service, config


def test_complete_api_flow_and_sse(api) -> None:
    client, service, config = api
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/v1/system/models").json()["verifier"] == "glm-5.3"
    response = client.post(
        "/api/v1/tasks",
        data={"report_type": "work_summary", "period": "2026-W35"},
        files=[("files", ("weekly.md", "完成 Atlas 发布。".encode(), "text/markdown"))],
    )
    assert response.status_code == 202
    task_id = response.json()["task_id"]
    wait_for(client, task_id)
    assert client.get("/api/v1/tasks").json()[0]["task_id"] == task_id
    assert client.get(f"/api/v1/tasks/{task_id}/materials").status_code == 200
    assert client.get(f"/api/v1/tasks/{task_id}/facts").json()["facts"]
    draft = client.get(f"/api/v1/tasks/{task_id}/draft").json()
    assert "核心成果" in draft["markdown"]
    assert client.get(f"/api/v1/tasks/{task_id}/usage").json() == []
    assert client.get(f"/api/v1/tasks/{task_id}/history").json()
    assert client.get(f"/api/v1/tasks/{task_id}/artifacts/draft.md").status_code == 200
    assert client.get(f"/api/v1/tasks/{task_id}/artifacts/report.md").status_code == 409
    completed = client.post(f"/api/v1/tasks/{task_id}/decisions", json={"decision": "approve"})
    assert completed.json()["status"] == "completed"
    assert client.get(f"/api/v1/tasks/{task_id}/artifacts/report.md").status_code == 200
    stream = client.get(f"/api/v1/tasks/{task_id}/events")
    assert "event: stage" in stream.text


def test_api_errors_edit_reject_retry_and_cancel(api) -> None:
    client, service, config = api
    assert client.get("/api/v1/tasks/missing").status_code == 404
    bad = client.post(
        "/api/v1/tasks", data={"report_type": "work_summary", "period": "p"},
        files=[("files", ("bad.pdf", b"x", "application/pdf"))],
    )
    assert bad.status_code == 400
    duplicate = client.post(
        "/api/v1/tasks", data={"report_type": "work_summary", "period": "p"},
        files=[("files", ("same.txt", b"a", "text/plain")), ("files", ("same.txt", b"b", "text/plain"))],
    )
    assert duplicate.status_code == 400
    response = client.post(
        "/api/v1/tasks", data={"report_type": "work_summary", "period": "p"},
        files=[("files", ("x.txt", b"done", "text/plain"))],
    )
    task_id = response.json()["task_id"]
    wait_for(client, task_id)
    assert client.post(f"/api/v1/tasks/{task_id}/decisions", json={"decision": "edit"}).status_code == 409
    rejected = client.post(f"/api/v1/tasks/{task_id}/decisions", json={"decision": "reject"})
    assert rejected.json()["status"] == "rejected"
    assert client.post(f"/api/v1/tasks/{task_id}/cancel").status_code == 409
    assert client.post(f"/api/v1/tasks/{task_id}/retry").status_code == 409
    assert client.get(f"/api/v1/tasks/{task_id}/artifacts/nope").status_code == 404


def test_evaluation_api(api, monkeypatch, tmp_path) -> None:
    client, service, config = api
    monkeypatch.setattr("workpilot.evaluation.run_evaluation", lambda *args: tmp_path / "result")
    created = client.post("/api/v1/evaluations", json={"split": "dev", "system": "both"})
    assert created.status_code == 202
    evaluation_id = created.json()["evaluation_id"]
    for _ in range(100):
        result = client.get(f"/api/v1/evaluations/{evaluation_id}").json()
        if result["status"] == "completed":
            break
        time.sleep(0.01)
    assert result["output"].endswith("result")
    assert client.get("/api/v1/evaluations/missing").status_code == 404


def test_service_concurrent_isolation_retry_cancel_and_retention(tmp_path, monkeypatch) -> None:
    config = settings(tmp_path, monkeypatch)
    monkeypatch.setattr("workpilot.service.run_pipeline", fake_pipeline)
    service = ApplicationService(config)
    records = [service.submit("work_summary", "p", [(f"{index}.txt", f"done {index}".encode())]) for index in range(3)]
    for record in records:
        for _ in range(100):
            if service.require(record.task_id).status is RunStatus.WAITING_APPROVAL:
                break
            time.sleep(0.02)
        assert service.require(record.task_id).status is RunStatus.WAITING_APPROVAL
        assert (config.workspace_root / record.task_id / "drafts" / "report-v2.json").exists()

    cancelled = service.cancel(records[0].task_id)
    assert cancelled.status is RunStatus.CANCELLED
    service.store.update(records[1].task_id, status=RunStatus.FAILED, stage=WorkflowStage.FAILED)
    retry = service.retry(records[1].task_id)
    assert retry.status is RunStatus.RECOVERING
    for _ in range(100):
        if service.require(records[1].task_id).status is RunStatus.WAITING_APPROVAL:
            break
        time.sleep(0.02)
    assert service.require(records[1].task_id).status is RunStatus.WAITING_APPROVAL

    old = service.store.create("old", "work_summary", "p")
    service.store.update(old.task_id, status=RunStatus.COMPLETED, stage=WorkflowStage.COMPLETED)
    old_dir = config.workspace_root / "old"
    old_dir.mkdir(parents=True)
    past = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    with sqlite3.connect(config.task_db_path) as connection:
        connection.execute("UPDATE tasks SET updated_at=? WHERE task_id='old'", (past,))
    assert service.cleanup_expired() == ["old"]
    assert not old_dir.exists()
    assert service.store.get("old") is None
    service.shutdown()

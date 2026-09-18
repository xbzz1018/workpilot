"""Persistent WorkPilot run lifecycle and HITL decisions."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Literal

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from langchain_core.messages import ToolMessage

from workpilot.agents import build_run_prompt, build_workpilot_agent
from workpilot.audit import UsageCollector
from workpilot.gate import sections_from_markdown, validate_report
from workpilot.ingestion import import_package
from workpilot.schemas import (
    FactExtraction,
    ImportedPackage,
    ReportBundle,
    RunOutcome,
    RunStatus,
    TaskSpec,
    VerificationResult,
)
from workpilot.workspace import TaskWorkspace


class WorkflowError(RuntimeError):
    pass


def new_thread_id() -> str:
    return uuid.uuid4().hex[:16]


def start_run(
    input_dir: str | Path,
    workspace_root: str | Path,
    report_type: str,
    period: str,
    thread_id: str | None = None,
    model: Any | None = None,
) -> RunOutcome:
    thread_id = thread_id or new_thread_id()
    task = TaskSpec(task_id=thread_id, report_type=report_type, period=period)
    workspace = TaskWorkspace(workspace_root, task.task_id)
    if workspace.exists("task.json"):
        existing_task = TaskSpec.model_validate(workspace.read_json("task.json"))
        existing_package = ImportedPackage.model_validate(workspace.read_json("intermediate/materials.json"))
        current_package = import_package(input_dir)
        if existing_task != task or existing_package != current_package:
            raise WorkflowError("existing task metadata or input hashes do not match the recovery request")
        status = workspace.read_json("status.json").get("status") if workspace.exists("status.json") else None
        if status != RunStatus.FAILED:
            raise WorkflowError(f"task already exists with status: {status or 'unknown'}")
        return _invoke_until_interrupt(workspace, task, existing_package, thread_id, model, recover=True)
    package = import_package(input_dir)
    workspace.write_task(task)
    workspace.stage_input(input_dir, package)
    return _invoke_until_interrupt(workspace, task, package, thread_id, model)


def _invoke_until_interrupt(
    workspace: TaskWorkspace,
    task: TaskSpec,
    package: ImportedPackage,
    thread_id: str,
    model: Any | None,
    recover: bool = False,
) -> RunOutcome:
    collector = UsageCollector()
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}
    try:
        with SqliteSaver.from_conn_string(str(workspace.db_path)) as checkpointer:
            agent = build_workpilot_agent(workspace, task, package, collector, checkpointer, model=model)
            invocation = None if recover else {"messages": [{"role": "user", "content": build_run_prompt(task, package)}]}
            agent.invoke(invocation, config=config)
            state = agent.get_state(config)
            action = _pending_delivery(state)
            _persist_subagent_outputs(workspace, state)
    except Exception as exc:
        _append_usage(workspace, collector)
        _write_status(workspace, thread_id, RunStatus.FAILED, f"{type(exc).__name__}: {exc}")
        raise
    _append_usage(workspace, collector)
    if action is None:
        _write_status(workspace, thread_id, RunStatus.FAILED, "Agent ended without requesting delivery")
        raise WorkflowError("agent ended without a request_delivery interrupt")
    report = _report_from_action(action)
    validate_report(report, task, package.evidence)
    _write_draft(workspace, report)
    return _write_status(workspace, thread_id, RunStatus.WAITING_APPROVAL, "Draft passed gate and awaits approval")


def resume_run(
    workspace_root: str | Path,
    thread_id: str,
    decision: Literal["approve", "edit", "reject"],
    edited_file: str | Path | None = None,
    message: str | None = None,
    model: Any | None = None,
) -> RunOutcome:
    workspace = TaskWorkspace(workspace_root, thread_id)
    task = TaskSpec.model_validate(workspace.read_json("task.json"))
    package = ImportedPackage.model_validate(workspace.read_json("intermediate/materials.json"))
    collector = UsageCollector()
    config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}
    with SqliteSaver.from_conn_string(str(workspace.db_path)) as checkpointer:
        agent = build_workpilot_agent(workspace, task, package, collector, checkpointer, model=model)
        action = _pending_delivery(agent.get_state(config))
        if action is None:
            raise WorkflowError("thread is not waiting for approval")
        report = _report_from_action(action)
        if decision == "edit":
            if edited_file is None:
                raise WorkflowError("--edited-file is required for edit")
            markdown = Path(edited_file).read_text(encoding="utf-8")
            report = report.model_copy(update={
                "markdown": markdown,
                "sections": sections_from_markdown(markdown, report.sections),
            })
            validate_report(report, task, package.evidence)
            resume_decision: dict[str, Any] = {
                "type": "edit",
                "edited_action": {"name": "request_delivery", "args": {"report": report.model_dump(mode="json")}},
            }
        elif decision == "reject":
            resume_decision = {"type": "reject", "message": message or "Report rejected by user"}
        else:
            resume_decision = {"type": "approve"}
        agent.invoke(Command(resume={"decisions": [resume_decision]}), config=config)
        next_action = _pending_delivery(agent.get_state(config))
    _append_usage(workspace, collector)

    if decision == "reject":
        return _write_status(workspace, thread_id, RunStatus.REJECTED, message or "Report rejected by user")
    if next_action is not None:
        revised = _report_from_action(next_action)
        validate_report(revised, task, package.evidence)
        _write_draft(workspace, revised)
        return _write_status(workspace, thread_id, RunStatus.WAITING_APPROVAL, "Agent requested approval for a revised draft")
    validate_report(report, task, package.evidence)
    workspace.write_json("final/report.json", report)
    workspace.write_text("final/report.md", report.markdown)
    return _write_status(workspace, thread_id, RunStatus.COMPLETED, "Final report exported")


def get_history(workspace_root: str | Path, thread_id: str, model: Any | None = None) -> list[dict[str, Any]]:
    workspace = TaskWorkspace(workspace_root, thread_id)
    task = TaskSpec.model_validate(workspace.read_json("task.json"))
    package = ImportedPackage.model_validate(workspace.read_json("intermediate/materials.json"))
    config = {"configurable": {"thread_id": thread_id}}
    if model is None:
        from workpilot.model import create_model

        model = create_model(allow_placeholder=True)
    with SqliteSaver.from_conn_string(str(workspace.db_path)) as checkpointer:
        agent = build_workpilot_agent(workspace, task, package, UsageCollector(), checkpointer, model=model)
        return [
            {
                "checkpoint_id": state.config["configurable"].get("checkpoint_id"),
                "step": state.metadata.get("step"),
                "source": state.metadata.get("source"),
                "next": list(state.next),
            }
            for state in agent.get_state_history(config)
        ]


def _pending_delivery(state: Any) -> dict[str, Any] | None:
    for task in state.tasks:
        for interrupt in task.interrupts:
            value = interrupt.value if isinstance(interrupt.value, dict) else {}
            for request in value.get("action_requests", []):
                if request.get("name") == "request_delivery":
                    return request
    return None


def _persist_subagent_outputs(workspace: TaskWorkspace, state: Any) -> None:
    extraction: FactExtraction | None = None
    verification: VerificationResult | None = None
    for message in state.values.get("messages", []):
        if not isinstance(message, ToolMessage) or message.name != "task" or not isinstance(message.content, str):
            continue
        try:
            payload = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or "facts" not in payload:
            continue
        if "conflicts" in payload and "risks" in payload:
            verification = VerificationResult.model_validate(payload)
        elif extraction is None:
            extraction = FactExtraction.model_validate(payload)
    if extraction is None or verification is None:
        raise WorkflowError("required structured subagent outputs are missing from the task trace")
    workspace.write_json("intermediate/facts.json", extraction)
    workspace.write_json("intermediate/verification.json", verification)


def _report_from_action(action: dict[str, Any]) -> ReportBundle:
    arguments = action.get("arguments", action.get("args", {}))
    value = arguments.get("report") if isinstance(arguments, dict) else None
    if value is None:
        raise WorkflowError("request_delivery interrupt is missing report arguments")
    return ReportBundle.model_validate(value)


def _write_draft(workspace: TaskWorkspace, report: ReportBundle) -> None:
    workspace.write_json("drafts/report.json", report)
    workspace.write_text("drafts/report.md", report.markdown)


def _append_usage(workspace: TaskWorkspace, collector: UsageCollector) -> None:
    previous = workspace.read_json("usage.json") if workspace.exists("usage.json") else []
    previous.extend(record.model_dump(mode="json") for record in collector.records)
    workspace.write_json("usage.json", previous)


def _write_status(
    workspace: TaskWorkspace,
    thread_id: str,
    status: RunStatus,
    message: str,
) -> RunOutcome:
    outcome = RunOutcome(
        task_id=workspace.task_id,
        thread_id=thread_id,
        status=status,
        workspace=str(workspace.path),
        message=message,
    )
    workspace.write_json("status.json", outcome)
    return outcome

"""Code-enforced v2 pipeline composed from three DeepAgents harnesses."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel, ValidationError

from workpilot.agents import build_stage_agents
from workpilot.audit import ModelCallBudget, UsageCollector
from workpilot.gate import validate_report
from workpilot.model import model_identity
from workpilot.prompting import prompt_versions
from workpilot.profiles import register_workpilot_profiles
from workpilot.renderer import render_report
from workpilot.schemas import (
    FactExtraction,
    ImportedPackage,
    ModelRole,
    ReportBundle,
    ReportDraft,
    TaskSpec,
    UsageRecord,
    ValidationResult,
    VerificationResult,
    WorkflowStage,
)
from workpilot.settings import RoleModelSettings, Settings
from workpilot.validation import validate_facts
from workpilot.workspace import TaskWorkspace

StageCallback = Callable[[WorkflowStage, str], None]
CancelCheck = Callable[[], bool]


class PipelineCancelled(RuntimeError):
    pass


def run_pipeline(
    workspace: TaskWorkspace,
    task: TaskSpec,
    package: ImportedPackage,
    *,
    settings: Settings | None = None,
    models: dict[ModelRole, Any] | None = None,
    audit_settings: dict[ModelRole, RoleModelSettings] | None = None,
    budget: ModelCallBudget | None = None,
    on_stage: StageCallback | None = None,
    is_cancelled: CancelCheck | None = None,
) -> ReportBundle:
    collector = UsageCollector()

    def emit(stage: WorkflowStage, message: str) -> None:
        if is_cancelled and is_cancelled():
            raise PipelineCancelled("task cancellation requested")
        if on_stage:
            on_stage(stage, message)

    settings = settings or Settings.from_env(allow_placeholder=models is not None)
    try:
        with SqliteSaver.from_conn_string(str(workspace.db_path)) as checkpointer:
            agents = build_stage_agents(
                workspace,
                collector,
                checkpointer,
                settings=settings,
                models=models,
                audit_settings=audit_settings,
                budget=budget,
            )

            emit(WorkflowStage.EXTRACTING, "Extracting evidence-bound facts")
            if workspace.exists("intermediate/facts-v2.json"):
                extraction = FactExtraction.model_validate(workspace.read_json("intermediate/facts-v2.json"))
            else:
                extraction = _invoke_structured(
                    agents[ModelRole.EXTRACTOR],
                    _extract_prompt(task, package),
                    {"configurable": {"thread_id": f"{task.task_id}:extractor"}, "recursion_limit": 20},
                    FactExtraction,
                    collector,
                )
                workspace.write_json("intermediate/facts-v2.json", extraction)

            emit(WorkflowStage.VALIDATING, "Running deterministic fact validation")
            if workspace.exists("intermediate/validation-v2.json"):
                validation = ValidationResult.model_validate(workspace.read_json("intermediate/validation-v2.json"))
            else:
                validation = validate_facts(extraction.facts, package.evidence, task)
                workspace.write_json("intermediate/validation-v2.json", validation)

            emit(WorkflowStage.VERIFYING, "Running independent semantic verification")
            if workspace.exists("intermediate/verification-v2.json"):
                verification = VerificationResult.model_validate(workspace.read_json("intermediate/verification-v2.json"))
            else:
                verification = _invoke_structured(
                    agents[ModelRole.VERIFIER],
                    _verify_prompt(task, package, validation),
                    {"configurable": {"thread_id": f"{task.task_id}:verifier"}, "recursion_limit": 20},
                    VerificationResult,
                    collector,
                )
                workspace.write_json("intermediate/verification-v2.json", verification)

            emit(WorkflowStage.DRAFTING, "Composing claims from verified facts")
            if workspace.exists("drafts/claims-v2.json"):
                draft = ReportDraft.model_validate(workspace.read_json("drafts/claims-v2.json"))
            else:
                draft = _invoke_structured(
                    agents[ModelRole.MAIN],
                    _draft_prompt(task, verification),
                    {"configurable": {"thread_id": f"{task.task_id}:main"}, "recursion_limit": 20},
                    ReportDraft,
                    collector,
                )
                workspace.write_json("drafts/claims-v2.json", draft)

        report = render_report(
            task,
            draft,
            verification.facts,
            package.evidence,
            verification.conflicts,
            verification.risks,
            model_identity(models[ModelRole.MAIN]) if models else settings.main.model,
        )
        validate_report(report, task, package.evidence)
        workspace.write_json("drafts/report-v2.json", report)
        workspace.write_text("drafts/report-v2.md", report.markdown)
        workspace.write_json("intermediate/harness-versions.json", {
            "prompts": [item.model_dump(mode="json") for item in prompt_versions()],
            "profile": register_workpilot_profiles(settings).model_dump(mode="json"),
        })
        emit(WorkflowStage.AWAITING_APPROVAL, "Draft passed all gates and awaits approval")
        return report
    finally:
        if collector.records:
            _append_usage(workspace, collector.records)


def approve_pipeline(workspace: TaskWorkspace, decision: str, edited_markdown: str | None = None) -> ReportBundle | None:
    report = ReportBundle.model_validate(workspace.read_json("drafts/report-v2.json"))
    task = TaskSpec.model_validate(workspace.read_json("task.json"))
    package = ImportedPackage.model_validate(workspace.read_json("intermediate/materials.json"))
    if decision == "reject":
        workspace.write_json("decision.json", {"decision": "reject"})
        return None
    if decision == "edit":
        if edited_markdown is None:
            raise ValueError("edited_markdown is required for edit")
        from workpilot.gate import sections_from_markdown

        report = report.model_copy(update={"markdown": edited_markdown, "sections": sections_from_markdown(edited_markdown, report.sections)})
    validate_report(report, task, package.evidence)
    workspace.write_json("decision.json", {"decision": decision})
    workspace.write_json("final/report.json", report)
    workspace.write_text("final/report.md", report.markdown)
    return report


def _extract_prompt(task: TaskSpec, package: ImportedPackage) -> str:
    return json.dumps({"task": task.model_dump(mode="json"), "evidence": [item.model_dump(mode="json") for item in package.evidence]}, ensure_ascii=False)


def _verify_prompt(task: TaskSpec, package: ImportedPackage, validation: ValidationResult) -> str:
    return json.dumps({"task": task.model_dump(mode="json"), "facts": [item.model_dump(mode="json") for item in validation.facts], "deterministic_issues": [item.model_dump(mode="json") for item in validation.issues], "evidence": [item.model_dump(mode="json") for item in package.evidence]}, ensure_ascii=False)


def _draft_prompt(task: TaskSpec, verification: VerificationResult) -> str:
    return json.dumps({"task": task.model_dump(mode="json"), "verified": verification.model_dump(mode="json")}, ensure_ascii=False)


def _append_usage(workspace: TaskWorkspace, records: list[UsageRecord]) -> None:
    previous = workspace.read_json("usage.json") if workspace.exists("usage.json") else []
    previous.extend(item.model_dump(mode="json") for item in records)
    workspace.write_json("usage.json", previous)


def _invoke_structured(
    agent: Any,
    prompt: str,
    config: dict,
    schema: type[BaseModel],
    collector: UsageCollector,
    max_attempts: int = 3,
) -> BaseModel:
    last_error: ValidationError | None = None
    for attempt in range(max_attempts):
        before = len(collector.records)
        suffix = "" if attempt == 0 else "\n\nPrevious response was not valid structured output. Return the required schema now."
        result = agent.invoke({"messages": [{"role": "user", "content": prompt + suffix}]}, config=config)
        collector.mark_since(before, attempt)
        try:
            return schema.model_validate(result.get("structured_response"))
        except ValidationError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error

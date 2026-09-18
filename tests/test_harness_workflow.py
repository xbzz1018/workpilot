from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from workpilot.audit import UsageCollector
from workpilot.ingestion import import_package
from workpilot.schemas import (
    Fact,
    ReportBundle,
    ReportMetadata,
    ReportSection,
    RunStatus,
    TaskSpec,
    ReportClaim,
    FactExtraction,
)
from workpilot.pipeline import _invoke_structured, run_pipeline
from workpilot.settings import Settings
from workpilot.schemas import ModelRole
from workpilot.workflow import get_history, resume_run, start_run


class HarnessFakeModel(BaseChatModel):
    report: ReportBundle
    fail_verifier: bool = False
    _bound_tools: list[str] = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "fake-harness"

    @property
    def model_name(self) -> str:
        return "fake-harness"

    def bind_tools(self, tools: list[Any], *, tool_choice: Any = None, **kwargs: Any) -> HarnessFakeModel:
        bound = self.model_copy(deep=True)
        bound._bound_tools = [
            getattr(item, "name", None)
            or item.get("function", {}).get("name")
            or item.get("name")
            for item in tools
        ]
        return bound

    def _generate(self, messages: list[Any], stop: list[str] | None = None, run_manager: Any = None, **kwargs: Any) -> ChatResult:
        fact = self.report.facts[0]
        if "FactExtraction" in self._bound_tools:
            message = self._tool_call("FactExtraction", {"facts": [fact.model_dump(mode="json")]}, "extract-result")
        elif "VerificationResult" in self._bound_tools:
            if self.fail_verifier:
                raise RuntimeError("injected verifier failure")
            message = self._tool_call(
                "VerificationResult",
                {"facts": [fact.model_dump(mode="json")], "conflicts": [], "risks": []},
                "verify-result",
            )
        elif "ReportDraft" in self._bound_tools:
            fact_id = fact.fact_id
            evidence_id = fact.evidence_ids[0]
            message = self._tool_call(
                "ReportDraft",
                {"claims": [ReportClaim(
                    claim_id="claim_1",
                    section="核心成果",
                    text=fact.statement,
                    fact_ids=[fact_id],
                    evidence_ids=[evidence_id],
                ).model_dump(mode="json")], "non_factual_notes": {}},
                "draft-result",
            )
        else:
            delivery_results = [item for item in messages if isinstance(item, ToolMessage) and item.name == "request_delivery"]
            task_results = [item for item in messages if isinstance(item, ToolMessage) and item.name == "task"]
            if delivery_results:
                message = self._message("Delivery decision processed.")
            elif len(task_results) == 0:
                message = self._tool_call(
                    "task",
                    {"description": "Extract every fact from the supplied evidence JSON.", "subagent_type": "achievement_extractor"},
                    "delegate-extract",
                )
            elif len(task_results) == 1:
                message = self._tool_call(
                    "task",
                    {"description": "Verify the extracted fact against the supplied evidence JSON.", "subagent_type": "fact_verifier"},
                    "delegate-verify",
                )
            else:
                message = self._tool_call(
                    "request_delivery",
                    {"report": self.report.model_dump(mode="json")},
                    "request-delivery",
                )
        return ChatResult(generations=[ChatGeneration(message=message)])

    @staticmethod
    def _message(content: str, tool_calls: list[dict[str, Any]] | None = None) -> AIMessage:
        return AIMessage(
            content=content,
            tool_calls=tool_calls or [],
            usage_metadata={
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "input_token_details": {"cache_read": 0},
            },
        )

    @classmethod
    def _tool_call(cls, name: str, args: dict[str, Any], call_id: str) -> AIMessage:
        return cls._message("", [{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def make_report(task: TaskSpec, evidence_id: str) -> ReportBundle:
    fact = Fact(
        fact_id=f"fact_{evidence_id.removeprefix('ev_')}",
        statement="完成 Atlas 发布检查并按计划上线",
        evidence_ids=[evidence_id],
        subject="Atlas 发布",
        predicate="按计划上线",
    )
    citation = f"[fact:{fact.fact_id}] [evidence:{evidence_id}]"
    headings = ["工作概览", "核心成果", "风险与待确认", "下一步计划"]
    sections = [
        ReportSection(
            heading=heading,
            body=f"完成发布 {citation}" if heading == "核心成果" else "无补充。",
            fact_ids=[fact.fact_id] if heading == "核心成果" else [],
            evidence_ids=[evidence_id] if heading == "核心成果" else [],
        )
        for heading in headings
    ]
    return ReportBundle(
        metadata=ReportMetadata(
            task_id=task.task_id,
            report_type=task.report_type,
            period=task.period,
            model="fake-harness",
            generated_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        ),
        facts=[fact],
        sections=sections,
        markdown="\n\n".join(f"## {section.heading}\n\n{section.body}" for section in sections),
    )


def prepare(tmp_path: Path, thread_id: str) -> tuple[Path, Path, HarnessFakeModel]:
    input_dir = tmp_path / f"input-{thread_id}"
    input_dir.mkdir()
    (input_dir / "weekly.md").write_text("完成 Atlas 发布检查并按计划上线。\n", encoding="utf-8")
    package = import_package(input_dir)
    task = TaskSpec(task_id=thread_id, report_type="work_summary", period="2026-W35")
    report = make_report(task, package.evidence[0].evidence_id)
    return input_dir, tmp_path / "workspace", HarnessFakeModel(report=report)


def test_harness_enters_hitl_and_recovers_to_approve(tmp_path: Path) -> None:
    input_dir, workspace, model = prepare(tmp_path, "flow-approve")
    first = start_run(input_dir, workspace, "work_summary", "2026-W35", "flow-approve", model=model)
    assert first.status is RunStatus.WAITING_APPROVAL
    task_dir = workspace / "flow-approve"
    assert (task_dir / "drafts/report.json").exists()
    assert (task_dir / "intermediate/facts.json").exists()
    assert (task_dir / "intermediate/verification.json").exists()
    assert not (task_dir / "final/report.json").exists()

    fresh_model = HarnessFakeModel(report=model.report)
    second = resume_run(workspace, "flow-approve", "approve", model=fresh_model)
    assert second.status is RunStatus.COMPLETED
    assert (task_dir / "final/report.md").exists()
    assert (task_dir / "checkpoint.sqlite").exists()
    usage = __import__("json").loads((task_dir / "usage.json").read_text(encoding="utf-8"))
    names = {item["agent_name"] for item in usage}
    assert names == {"main", "achievement_extractor", "fact_verifier"}


def test_harness_reject_keeps_draft_without_final(tmp_path: Path) -> None:
    input_dir, workspace, model = prepare(tmp_path, "flow-reject")
    start_run(input_dir, workspace, "work_summary", "2026-W35", "flow-reject", model=model)
    outcome = resume_run(
        workspace,
        "flow-reject",
        "reject",
        message="Numbers need confirmation",
        model=HarnessFakeModel(report=model.report),
    )
    assert outcome.status is RunStatus.REJECTED
    task_dir = workspace / "flow-reject"
    assert (task_dir / "drafts/report.json").exists()
    assert not (task_dir / "final/report.json").exists()


def test_harness_edit_and_history(tmp_path: Path, monkeypatch) -> None:
    input_dir, workspace, model = prepare(tmp_path, "flow-edit")
    start_run(input_dir, workspace, "work_summary", "2026-W35", "flow-edit", model=model)
    edited = tmp_path / "edited.md"
    edited.write_text(model.report.markdown.replace("无补充。", "人工确认无补充。", 1), encoding="utf-8")
    outcome = resume_run(
        workspace,
        "flow-edit",
        "edit",
        edited_file=edited,
        model=HarnessFakeModel(report=model.report),
    )
    assert outcome.status is RunStatus.COMPLETED
    assert "人工确认" in (workspace / "flow-edit" / "final/report.md").read_text(encoding="utf-8")
    history = get_history(workspace, "flow-edit", model=HarnessFakeModel(report=model.report))
    assert history
    assert all("checkpoint_id" in item for item in history)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert get_history(workspace, "flow-edit")


def test_recovers_after_extraction_without_repeating_subagent(tmp_path: Path) -> None:
    input_dir, workspace, model = prepare(tmp_path, "flow-recover")
    failing = HarnessFakeModel(report=model.report, fail_verifier=True)
    with __import__("pytest").raises(RuntimeError, match="injected verifier"):
        start_run(input_dir, workspace, "work_summary", "2026-W35", "flow-recover", model=failing)
    task_dir = workspace / "flow-recover"
    status = __import__("json").loads((task_dir / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"

    outcome = start_run(
        input_dir,
        workspace,
        "work_summary",
        "2026-W35",
        "flow-recover",
        model=HarnessFakeModel(report=model.report),
    )
    assert outcome.status is RunStatus.WAITING_APPROVAL
    usage = __import__("json").loads((task_dir / "usage.json").read_text(encoding="utf-8"))
    extractor_calls = [item for item in usage if item["agent_name"] == "achievement_extractor"]
    assert len(extractor_calls) == 1


def test_v2_pipeline_enforces_stages_and_reuses_artifacts(tmp_path: Path) -> None:
    input_dir, workspace_root, model = prepare(tmp_path, "flow-v2")
    package = import_package(input_dir)
    task = TaskSpec(task_id="flow-v2", report_type="work_summary", period="2026-W35")
    workspace = __import__("workpilot.workspace", fromlist=["TaskWorkspace"]).TaskWorkspace(workspace_root, task.task_id)
    workspace.write_task(task)
    workspace.stage_input(input_dir, package)
    settings = Settings.from_env(allow_placeholder=True)
    models = {role: HarnessFakeModel(report=model.report) for role in ModelRole}
    stages = []
    report = run_pipeline(workspace, task, package, settings=settings, models=models, on_stage=lambda stage, _: stages.append(stage))
    assert report.markdown.startswith("## 工作概览")
    assert stages == ["extracting", "validating", "verifying", "drafting", "awaiting_approval"]
    assert workspace.exists("intermediate/facts-v2.json")
    assert workspace.exists("intermediate/validation-v2.json")
    assert workspace.exists("intermediate/verification-v2.json")
    assert workspace.exists("drafts/claims-v2.json")
    second = run_pipeline(workspace, task, package, settings=settings, models=models)
    assert second.markdown == report.markdown
    assert second.facts == report.facts


def test_structured_stage_retries_missing_response() -> None:
    class Agent:
        calls = 0

        def invoke(self, payload, config):
            self.calls += 1
            if self.calls == 1:
                return {"structured_response": None}
            return {"structured_response": {"facts": []}}

    agent = Agent()
    result = _invoke_structured(agent, "prompt", {}, FactExtraction, UsageCollector())
    assert result == FactExtraction(facts=[])
    assert agent.calls == 2

from __future__ import annotations

from datetime import datetime, timezone
import hashlib

import pytest

from workpilot.schemas import (
    Evidence,
    Fact,
    Material,
    ReportBundle,
    ReportMetadata,
    ReportSection,
    TaskSpec,
)


@pytest.fixture
def task() -> TaskSpec:
    return TaskSpec(task_id="task-1", report_type="work_summary", period="2026-W35")


@pytest.fixture
def evidence() -> Evidence:
    excerpt = "完成 Atlas 发布检查并按计划上线。"
    return Evidence(
        evidence_id="ev_1111111111111111",
        material_id="mat_2222222222222222",
        source_path="weekly.md",
        locator="line:1",
        excerpt=excerpt,
        sha256=hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
    )


@pytest.fixture
def material() -> Material:
    return Material(
        material_id="mat_2222222222222222",
        source_path="weekly.md",
        media_type="markdown",
        sha256="4" * 64,
        size_bytes=10,
    )


@pytest.fixture
def report(task: TaskSpec, evidence: Evidence) -> ReportBundle:
    fact = Fact(
        fact_id="fact_1111111111111111",
        statement="完成 Atlas 发布检查并按计划上线",
        evidence_ids=[evidence.evidence_id],
    )
    citation = f"[fact:{fact.fact_id}] [evidence:{evidence.evidence_id}]"
    headings = ["工作概览", "核心成果", "风险与待确认", "下一步计划"]
    sections = [
        ReportSection(
            heading=heading,
            body=("完成 Atlas 发布检查 " + citation) if heading == "核心成果" else "无待确认事项。",
            fact_ids=[fact.fact_id] if heading == "核心成果" else [],
            evidence_ids=[evidence.evidence_id] if heading == "核心成果" else [],
        )
        for heading in headings
    ]
    markdown = "\n\n".join(f"## {section.heading}\n\n{section.body}" for section in sections)
    return ReportBundle(
        metadata=ReportMetadata(
            task_id=task.task_id,
            report_type=task.report_type,
            period=task.period,
            model="fake-model",
            generated_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        ),
        facts=[fact],
        sections=sections,
        markdown=markdown,
    )

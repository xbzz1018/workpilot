from __future__ import annotations

import hashlib

import pytest

from workpilot.prompting import load_prompt, prompt_versions
from workpilot.renderer import render_report
from workpilot.schemas import Evidence, Fact, ReportClaim, ReportDraft, TaskSpec
from workpilot.validation import validate_claims, validate_facts


def atomic(evidence: Evidence, **updates) -> Fact:
    data = {
        "fact_id": "fact_atomic",
        "statement": "订单接口响应时间从800ms降至520ms",
        "category": "achievement",
        "evidence_ids": [evidence.evidence_id],
        "subject": "订单接口响应时间",
        "predicate": "下降",
        "value": 520,
        "unit": "ms",
    }
    data.update(updates)
    return Fact(**data)


@pytest.fixture
def numeric_evidence() -> Evidence:
    excerpt = "订单接口响应时间从800ms降至520ms"
    return Evidence(
        evidence_id="ev_aaaaaaaaaaaaaaaa", material_id="mat_bbbbbbbbbbbbbbbb",
        source_path="metrics.txt", locator="line:1", excerpt=excerpt,
        sha256=hashlib.sha256(excerpt.encode()).hexdigest(),
    )


def test_atomic_fact_and_claim_render(numeric_evidence: Evidence) -> None:
    task = TaskSpec(task_id="atomic", report_type="work_summary", period="2026-W35")
    fact = atomic(numeric_evidence)
    result = validate_facts([fact], [numeric_evidence], task)
    assert result.passed
    assert result.facts[0].period == task.period
    claim = ReportClaim(
        claim_id="claim_atomic", section="核心成果", text=fact.statement,
        fact_ids=[fact.fact_id], evidence_ids=fact.evidence_ids,
    )
    draft = ReportDraft(claims=[claim], non_factual_notes={"下一步计划": "继续观察。"})
    assert validate_claims(draft, result.facts, [numeric_evidence], task) == []
    report = render_report(task, draft, result.facts, [numeric_evidence], [], [], "model-x")
    assert "[fact:fact_atomic]" in report.markdown
    assert report.metadata.model == "model-x"


def test_fact_validator_collects_deterministic_failures(numeric_evidence: Evidence) -> None:
    task = TaskSpec(task_id="bad", report_type="work_summary", period="p")
    tampered = numeric_evidence.model_copy(update={"excerpt": numeric_evidence.excerpt + "x"})
    fact = atomic(
        numeric_evidence,
        statement="订单接口在2026-08-30降至999秒",
        subject=None,
        predicate=None,
        value=999,
        unit="秒",
        evidence_ids=[numeric_evidence.evidence_id, "ev_cccccccccccccccc"],
    )
    result = validate_facts([fact], [tampered], task)
    codes = {item.code for item in result.issues}
    assert {"unknown_evidence", "evidence_hash_mismatch", "non_atomic_fact", "unsupported_number_or_date", "unsupported_value", "unsupported_unit"}.issubset(codes)
    assert not result.passed
    assert result.facts[0].status == "unsupported"


def test_claim_validator_reports_every_binding_error(numeric_evidence: Evidence) -> None:
    task = TaskSpec(task_id="claim", report_type="work_summary", period="p")
    unsupported = atomic(numeric_evidence, status="unsupported")
    claims = [
        ReportClaim(claim_id="claim_dup", section="错误章节", text="新增2027年结果", fact_ids=[unsupported.fact_id, "fact_missing"], evidence_ids=["ev_cccccccccccccccc"]),
        ReportClaim(claim_id="claim_dup", section="核心成果", text="新增结果", fact_ids=[unsupported.fact_id], evidence_ids=[numeric_evidence.evidence_id]),
    ]
    errors = validate_claims(ReportDraft(claims=claims), [unsupported], [numeric_evidence], task)
    assert any("duplicate" in item for item in errors)
    assert any("unknown section" in item for item in errors)
    assert any("unknown fact" in item for item in errors)
    assert any("non-supported" in item for item in errors)
    assert any("not bound" in item for item in errors)
    assert any("number or date" in item for item in errors)
    with pytest.raises(ValueError, match="claim gate"):
        render_report(task, ReportDraft(claims=claims), [unsupported], [numeric_evidence], [], [], "m")


def test_prompt_resources_are_versioned() -> None:
    text, version = load_prompt("main")
    assert "ReportDraft" in text
    assert version.version == "main-v2.2"
    assert len(prompt_versions()) == 4

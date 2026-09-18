from __future__ import annotations

import pytest

from workpilot.gate import GateError, sections_from_markdown, validate_report
from workpilot.schemas import Conflict, Evidence, FactStatus, ReportBundle, Risk, TaskSpec


def test_valid_report_passes(report: ReportBundle, task: TaskSpec, evidence: Evidence) -> None:
    validate_report(report, task, [evidence])


def test_unknown_citation_fails(report: ReportBundle, task: TaskSpec, evidence: Evidence) -> None:
    changed = report.model_copy(update={"markdown": report.markdown + " [evidence:ev_aaaaaaaaaaaaaaaa]"})
    with pytest.raises(GateError, match="unknown evidence"):
        validate_report(changed, task, [evidence])


def test_forbidden_expression_fails(report: ReportBundle, task: TaskSpec, evidence: Evidence) -> None:
    changed = report.model_copy(update={"markdown": report.markdown + "\n100%完成"})
    with pytest.raises(GateError, match="100%"):
        validate_report(changed, task, [evidence])


def test_markdown_edit_rebuilds_sections(report: ReportBundle) -> None:
    edited = report.markdown.replace("无待确认事项。", "等待负责人确认。", 1)
    sections = sections_from_markdown(edited, report.sections)
    assert len(sections) == 4
    assert sections[0].body == "等待负责人确认。"
    assert sections[1].fact_ids == ["fact_1111111111111111"]


def test_gate_reports_metadata_missing_sections_and_bad_fact(report: ReportBundle, task: TaskSpec, evidence: Evidence) -> None:
    bad_fact = report.facts[0].model_copy(update={"evidence_ids": ["ev_aaaaaaaaaaaaaaaa"]})
    changed = report.model_copy(update={
        "metadata": report.metadata.model_copy(update={"task_id": "other", "period": "other"}),
        "facts": [bad_fact, bad_fact],
        "sections": report.sections[:1],
        "conflicts": [],
    })
    with pytest.raises(GateError) as raised:
        validate_report(changed, task, [evidence])
    message = str(raised.value)
    assert "task_id" in message
    assert "period" in message
    assert "unique" in message
    assert "missing required sections" in message


def test_gate_aggregates_conflict_risk_and_section_failures(report: ReportBundle, task: TaskSpec, evidence: Evidence) -> None:
    unsupported = report.facts[0].model_copy(update={"status": FactStatus.UNSUPPORTED})
    core = report.sections[1].model_copy(update={
        "fact_ids": [unsupported.fact_id, "fact_missing"],
        "evidence_ids": ["ev_aaaaaaaaaaaaaaaa"],
    })
    changed = report.model_copy(update={
        "metadata": report.metadata.model_copy(update={"report_type": "performance_review"}),
        "facts": [unsupported],
        "sections": [report.sections[0], core, *report.sections[2:]],
        "conflicts": [Conflict(
            conflict_id="conflict_bad",
            description="unknown evidence",
            evidence_ids=["ev_aaaaaaaaaaaaaaaa", "ev_bbbbbbbbbbbbbbbb"],
        )],
        "risks": [Risk(
            risk_id="risk_bad",
            kind="missing_evidence",
            description="unknown evidence",
            evidence_ids=["ev_aaaaaaaaaaaaaaaa"],
        )],
    })
    with pytest.raises(GateError) as raised:
        validate_report(changed, task, [evidence])
    message = str(raised.value)
    assert "report_type" in message
    assert "conflict_bad" in message
    assert "risk_bad" in message
    assert "unknown facts" in message
    assert "unsupported fact" in message


def test_conflicted_fact_must_be_disclosed_and_not_an_outcome(report: ReportBundle, task: TaskSpec, evidence: Evidence) -> None:
    conflicted = report.facts[0].model_copy(update={"status": FactStatus.CONFLICTED})
    changed = report.model_copy(update={"facts": [conflicted]})
    with pytest.raises(GateError) as raised:
        validate_report(changed, task, [evidence])
    assert "confirmed outcome" in str(raised.value)
    assert "not disclosed" in str(raised.value)


def test_evidence_hash_and_declared_citations_are_checked(report: ReportBundle, task: TaskSpec, evidence: Evidence) -> None:
    tampered = evidence.model_copy(update={"excerpt": evidence.excerpt + "tampered"})
    changed_section = report.sections[1].model_copy(update={"evidence_ids": []})
    changed = report.model_copy(update={"sections": [report.sections[0], changed_section, *report.sections[2:]]})
    with pytest.raises(GateError) as raised:
        validate_report(changed, task, [tampered])
    assert "hash mismatch" in str(raised.value)
    assert "do not match section" in str(raised.value)

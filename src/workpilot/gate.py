"""Deterministic delivery gate independent of model self-evaluation."""

from __future__ import annotations

import hashlib
import re

from workpilot.schemas import Evidence, FactStatus, ReportBundle, ReportSection, ReportType, RiskKind, TaskSpec

REQUIRED_HEADINGS = {
    ReportType.WORK_SUMMARY: ["工作概览", "核心成果", "风险与待确认", "下一步计划"],
    ReportType.PERFORMANCE_REVIEW: ["职责与范围", "核心成果", "证据与影响", "成长与不足", "下阶段计划"],
}
FORBIDDEN_PHRASES = ("业界第一", "行业第一", "100%", "零缺陷", "完全解决", "彻底解决", "显著提升")
EVIDENCE_CITATION = re.compile(r"\[evidence:(ev_[0-9a-f]{16})\]")
FACT_CITATION = re.compile(r"\[fact:(fact_[A-Za-z0-9._-]+)\]")


class GateError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("delivery gate failed: " + "; ".join(errors))


def validate_report(report: ReportBundle, task: TaskSpec, evidence: list[Evidence]) -> None:
    errors: list[str] = []
    evidence_by_id = {item.evidence_id: item for item in evidence}
    facts_by_id = {item.fact_id: item for item in report.facts}
    for item in evidence:
        actual_hash = hashlib.sha256(item.excerpt.encode("utf-8")).hexdigest()
        if actual_hash != item.sha256:
            errors.append(f"evidence excerpt hash mismatch: {item.evidence_id}")
    if report.metadata.task_id != task.task_id:
        errors.append("metadata task_id does not match task")
    if report.metadata.report_type != task.report_type:
        errors.append("metadata report_type does not match task")
    if report.metadata.period != task.period:
        errors.append("metadata period does not match task")
    if len(facts_by_id) != len(report.facts):
        errors.append("fact IDs must be unique")

    for fact in report.facts:
        missing = set(fact.evidence_ids) - evidence_by_id.keys()
        if missing:
            errors.append(f"{fact.fact_id} references unknown evidence: {sorted(missing)}")
    for conflict in report.conflicts:
        if set(conflict.evidence_ids) - evidence_by_id.keys():
            errors.append(f"{conflict.conflict_id} references unknown evidence")
    for risk in report.risks:
        if set(risk.evidence_ids) - evidence_by_id.keys():
            errors.append(f"{risk.risk_id} references unknown evidence")

    required = REQUIRED_HEADINGS[task.report_type]
    headings = [section.heading for section in report.sections]
    missing_headings = [heading for heading in required if heading not in headings]
    if missing_headings:
        errors.append(f"missing required sections: {missing_headings}")
    for heading in required:
        if not re.search(rf"(?m)^#+\s+{re.escape(heading)}\s*$", report.markdown):
            errors.append(f"markdown missing heading: {heading}")

    cited_evidence = set(EVIDENCE_CITATION.findall(report.markdown))
    cited_facts = set(FACT_CITATION.findall(report.markdown))
    if cited_evidence - evidence_by_id.keys():
        errors.append("markdown contains unknown evidence citations")
    if cited_facts - facts_by_id.keys():
        errors.append("markdown contains unknown fact citations")
    declared_evidence = {item for section in report.sections for item in section.evidence_ids}
    declared_facts = {item for section in report.sections for item in section.fact_ids}
    if cited_evidence != declared_evidence:
        errors.append("markdown evidence citations do not match section declarations")
    if cited_facts != declared_facts:
        errors.append("markdown fact citations do not match section declarations")
    for section in report.sections:
        if set(section.evidence_ids) - evidence_by_id.keys():
            errors.append(f"section {section.heading} references unknown evidence")
        if set(section.fact_ids) - facts_by_id.keys():
            errors.append(f"section {section.heading} references unknown facts")
        for fact_id in section.fact_ids:
            if fact_id not in facts_by_id:
                continue
            if facts_by_id[fact_id].status is FactStatus.UNSUPPORTED:
                errors.append(f"unsupported fact appears in section: {fact_id}")
            if facts_by_id[fact_id].status is FactStatus.CONFLICTED and section.heading not in {"风险与待确认", "成长与不足"}:
                errors.append(f"conflicted fact appears as a confirmed outcome: {fact_id}")

    conflicted_ids = {fact.fact_id for fact in report.facts if fact.status is FactStatus.CONFLICTED}
    risk_fact_text = " ".join(risk.description for risk in report.risks if risk.kind is RiskKind.CONFLICT)
    for fact_id in conflicted_ids:
        in_conflict = any(fact_id in conflict.fact_ids for conflict in report.conflicts)
        if not in_conflict and fact_id not in risk_fact_text:
            errors.append(f"conflicted fact is not disclosed: {fact_id}")
    for phrase in FORBIDDEN_PHRASES:
        if phrase.casefold() in report.markdown.casefold():
            errors.append(f"forbidden unsupported expression: {phrase}")
    if errors:
        raise GateError(errors)


def sections_from_markdown(markdown: str, original: list[ReportSection]) -> list[ReportSection]:
    """Apply human Markdown edits while preserving explicit fact/evidence bindings."""
    by_heading = {section.heading: section for section in original}
    matches = list(re.finditer(r"(?m)^#+\s+(.+?)\s*$", markdown))
    sections: list[ReportSection] = []
    for index, match in enumerate(matches):
        heading = match.group(1).strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        body = markdown[match.end():end].strip() or "(无补充)"
        previous = by_heading.get(heading)
        sections.append(ReportSection(
            heading=heading,
            body=body,
            fact_ids=list(FACT_CITATION.findall(body)) or (previous.fact_ids if previous else []),
            evidence_ids=list(EVIDENCE_CITATION.findall(body)) or (previous.evidence_ids if previous else []),
        ))
    return sections

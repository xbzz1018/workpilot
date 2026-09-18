"""Deterministic rendering of validated claims into report artifacts."""

from __future__ import annotations

from datetime import datetime, timezone

from workpilot.gate import REQUIRED_HEADINGS
from workpilot.schemas import (
    Conflict,
    Evidence,
    Fact,
    ReportBundle,
    ReportDraft,
    ReportMetadata,
    ReportSection,
    Risk,
    TaskSpec,
)
from workpilot.validation import validate_claims


def render_report(
    task: TaskSpec,
    draft: ReportDraft,
    facts: list[Fact],
    evidence: list[Evidence],
    conflicts: list[Conflict],
    risks: list[Risk],
    model: str,
) -> ReportBundle:
    errors = validate_claims(draft, facts, evidence, task)
    if errors:
        raise ValueError("claim gate failed: " + "; ".join(errors))
    sections: list[ReportSection] = []
    markdown_parts: list[str] = []
    for heading in REQUIRED_HEADINGS[task.report_type]:
        claims = [item for item in draft.claims if item.section == heading]
        if claims:
            lines = []
            fact_ids: list[str] = []
            evidence_ids: list[str] = []
            for claim in claims:
                citations = " ".join([*(f"[fact:{item}]" for item in claim.fact_ids), *(f"[evidence:{item}]" for item in claim.evidence_ids)])
                lines.append(f"- {claim.text} {citations}")
                fact_ids.extend(claim.fact_ids)
                evidence_ids.extend(claim.evidence_ids)
            body = "\n".join(lines)
        else:
            body = draft.non_factual_notes.get(heading, "无可核验内容。")
            fact_ids = []
            evidence_ids = []
        sections.append(ReportSection(
            heading=heading,
            body=body,
            fact_ids=list(dict.fromkeys(fact_ids)),
            evidence_ids=list(dict.fromkeys(evidence_ids)),
        ))
        markdown_parts.append(f"## {heading}\n\n{body}")
    return ReportBundle(
        metadata=ReportMetadata(
            task_id=task.task_id,
            report_type=task.report_type,
            period=task.period,
            model=model,
            generated_at=datetime.now(timezone.utc),
        ),
        facts=facts,
        conflicts=conflicts,
        risks=risks,
        sections=sections,
        markdown="\n\n".join(markdown_parts) + "\n",
    )


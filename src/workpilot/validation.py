"""Deterministic validation for atomic facts and claim-to-evidence bindings."""

from __future__ import annotations

import hashlib
import re

from workpilot.gate import REQUIRED_HEADINGS
from workpilot.schemas import (
    Evidence,
    Fact,
    FactStatus,
    ReportClaim,
    ReportDraft,
    TaskSpec,
    ValidationIssue,
    ValidationResult,
)

NUMBER = re.compile(r"(?<![A-Za-z0-9])\d+(?:\.\d+)?%?")
DATE = re.compile(r"(?:20\d{2}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?|\d{1,2}月\d{1,2}日)")


def _tokens(text: str) -> set[str]:
    return {token.replace("年", "-").replace("月", "-").replace("日", "").replace("/", "-") for token in NUMBER.findall(text) + DATE.findall(text)}


def validate_facts(facts: list[Fact], evidence: list[Evidence], task: TaskSpec, *, require_atomic: bool = True) -> ValidationResult:
    evidence_by_id = {item.evidence_id: item for item in evidence}
    issues: list[ValidationIssue] = []
    validated: list[Fact] = []
    for fact in facts:
        fact_issues: list[ValidationIssue] = []
        cited = [evidence_by_id[item] for item in fact.evidence_ids if item in evidence_by_id]
        missing = sorted(set(fact.evidence_ids) - evidence_by_id.keys())
        if missing:
            fact_issues.append(ValidationIssue(code="unknown_evidence", message=f"Unknown evidence IDs: {missing}", fact_id=fact.fact_id, evidence_ids=missing))
        for item in cited:
            if hashlib.sha256(item.excerpt.encode("utf-8")).hexdigest() != item.sha256:
                fact_issues.append(ValidationIssue(code="evidence_hash_mismatch", message="Evidence excerpt hash mismatch", fact_id=fact.fact_id, evidence_ids=[item.evidence_id]))
        if require_atomic and (not fact.subject or not fact.predicate):
            fact_issues.append(ValidationIssue(code="non_atomic_fact", message="Fact must include subject and predicate", fact_id=fact.fact_id, evidence_ids=fact.evidence_ids))
        evidence_text = "\n".join(item.excerpt for item in cited)
        unsupported_tokens = sorted(_tokens(fact.statement) - _tokens(evidence_text))
        if unsupported_tokens:
            fact_issues.append(ValidationIssue(code="unsupported_number_or_date", message=f"Values absent from evidence: {unsupported_tokens}", fact_id=fact.fact_id, evidence_ids=fact.evidence_ids))
        if fact.value is not None and str(fact.value) not in evidence_text:
            fact_issues.append(ValidationIssue(code="unsupported_value", message=f"Value {fact.value!r} is absent from evidence", fact_id=fact.fact_id, evidence_ids=fact.evidence_ids))
        if fact.unit and fact.unit.casefold() not in evidence_text.casefold():
            fact_issues.append(ValidationIssue(code="unsupported_unit", message=f"Unit {fact.unit!r} is absent from evidence", fact_id=fact.fact_id, evidence_ids=fact.evidence_ids))
        status = FactStatus.UNSUPPORTED if fact_issues else fact.status
        validated.append(fact.model_copy(update={"status": status, "period": fact.period or task.period}))
        issues.extend(fact_issues)
    return ValidationResult(passed=not issues, facts=validated, issues=issues)


def validate_claims(draft: ReportDraft, facts: list[Fact], evidence: list[Evidence], task: TaskSpec) -> list[str]:
    errors: list[str] = []
    facts_by_id = {item.fact_id: item for item in facts}
    evidence_ids = {item.evidence_id for item in evidence}
    allowed_sections = set(REQUIRED_HEADINGS[task.report_type])
    seen: set[str] = set()
    for claim in draft.claims:
        if claim.claim_id in seen:
            errors.append(f"duplicate claim ID: {claim.claim_id}")
        seen.add(claim.claim_id)
        if claim.section not in allowed_sections:
            errors.append(f"unknown section for {claim.claim_id}: {claim.section}")
        linked = [facts_by_id[item] for item in claim.fact_ids if item in facts_by_id]
        if len(linked) != len(claim.fact_ids):
            errors.append(f"unknown fact in claim: {claim.claim_id}")
        if any(item.status is not FactStatus.SUPPORTED for item in linked):
            errors.append(f"claim uses non-supported fact: {claim.claim_id}")
        allowed_evidence = {item for fact in linked for item in fact.evidence_ids}
        if not set(claim.evidence_ids).issubset(allowed_evidence & evidence_ids):
            errors.append(f"claim evidence is not bound to its facts: {claim.claim_id}")
        if not _tokens(claim.text).issubset(_tokens(" ".join(item.statement for item in linked))):
            errors.append(f"claim introduces a number or date absent from facts: {claim.claim_id}")
    return errors


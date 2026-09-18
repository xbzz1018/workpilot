"""Validated domain contracts shared by the workflow, CLI, and evaluator."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportType(StrEnum):
    WORK_SUMMARY = "work_summary"
    PERFORMANCE_REVIEW = "performance_review"


class FactStatus(StrEnum):
    SUPPORTED = "supported"
    CONFLICTED = "conflicted"
    UNSUPPORTED = "unsupported"


class RiskKind(StrEnum):
    CONFLICT = "conflict"
    MISSING_EVIDENCE = "missing_evidence"
    OUT_OF_PERIOD = "out_of_period"
    DUPLICATE = "duplicate"
    VALIDATION = "validation"


class TaskSpec(StrictModel):
    task_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    report_type: ReportType
    period: str = Field(min_length=1, max_length=120)
    title: str = Field(default="WorkPilot report", min_length=1, max_length=200)


class Material(StrictModel):
    material_id: str = Field(pattern=r"^mat_[0-9a-f]{16}$")
    source_path: str = Field(min_length=1)
    media_type: Literal["markdown", "text", "csv"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class Evidence(StrictModel):
    evidence_id: str = Field(pattern=r"^ev_[0-9a-f]{16}$")
    material_id: str = Field(pattern=r"^mat_[0-9a-f]{16}$")
    source_path: str = Field(min_length=1)
    locator: str = Field(min_length=1)
    excerpt: str = Field(min_length=1, max_length=8000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Fact(StrictModel):
    fact_id: str = Field(pattern=r"^fact_[A-Za-z0-9._-]+$")
    statement: str = Field(min_length=1, max_length=4000)
    category: str = Field(default="achievement", min_length=1, max_length=80)
    evidence_ids: list[str] = Field(min_length=1)
    status: FactStatus = FactStatus.SUPPORTED
    period: str | None = None
    subject: str | None = Field(default=None, max_length=240)
    predicate: str | None = Field(default=None, max_length=240)
    value: str | int | float | None = None
    unit: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def unique_evidence(self) -> Fact:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("fact evidence_ids must be unique")
        return self


class Conflict(StrictModel):
    conflict_id: str = Field(pattern=r"^conflict_[A-Za-z0-9._-]+$")
    description: str = Field(min_length=1, max_length=4000)
    fact_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(min_length=2)


class Risk(StrictModel):
    risk_id: str = Field(pattern=r"^risk_[A-Za-z0-9._-]+$")
    kind: RiskKind
    description: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = Field(default_factory=list)


class ReportSection(StrictModel):
    heading: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1)
    fact_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class ReportMetadata(StrictModel):
    task_id: str
    report_type: ReportType
    period: str
    model: str
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReportBundle(StrictModel):
    metadata: ReportMetadata
    facts: list[Fact]
    conflicts: list[Conflict] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)
    sections: list[ReportSection] = Field(min_length=1)
    markdown: str = Field(min_length=1)


class UsageRecord(StrictModel):
    call_id: str
    agent_name: Literal["main", "achievement_extractor", "fact_verifier", "baseline"]
    model: str
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0)
    duration_ms: float = Field(ge=0)
    pricing_version: str | None = None
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    requested_model: str | None = None
    actual_model: str | None = None
    key_alias: str | None = None
    retry_count: int = Field(default=0, ge=0)
    error_type: str | None = None


class QualityMetrics(StrictModel):
    fact_coverage: float = Field(ge=0, le=1)
    unsupported_claim_rate: float = Field(ge=0, le=1)
    citation_validity: float = Field(ge=0, le=1)
    conflict_detection: float | None = Field(default=None, ge=0, le=1)
    format_passed: bool
    task_succeeded: bool


class EvaluationResult(StrictModel):
    case_id: str
    system: Literal["baseline", "workpilot", "workpilot_single", "workpilot_heterogeneous", "challenger"]
    metrics: QualityMetrics
    usage: list[UsageRecord] = Field(default_factory=list)
    latency_ms: float = Field(ge=0)
    errors: list[str] = Field(default_factory=list)
    report: ReportBundle | None = None


class FactExtraction(StrictModel):
    facts: list[Fact] = Field(default_factory=list)


class VerificationResult(StrictModel):
    facts: list[Fact] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
    risks: list[Risk] = Field(default_factory=list)


class ImportedPackage(StrictModel):
    materials: list[Material] = Field(min_length=1)
    evidence: list[Evidence] = Field(min_length=1)


class RunStatus(StrEnum):
    QUEUED = "queued"
    INGESTING = "ingesting"
    EXTRACTING = "extracting"
    VALIDATING = "validating"
    VERIFYING = "verifying"
    DRAFTING = "drafting"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"
    RECOVERING = "recovering"
    CANCELLED = "cancelled"


class RunOutcome(StrictModel):
    task_id: str
    thread_id: str
    status: RunStatus
    workspace: str
    message: str


class GoldFact(StrictModel):
    gold_id: str
    statement: str
    evidence_ids: list[str] = Field(min_length=1)
    required: bool = True


class GoldConflict(StrictModel):
    conflict_id: str
    evidence_ids: list[str] = Field(min_length=2)


class GoldCase(StrictModel):
    case_id: str
    facts: list[GoldFact]
    forbidden_claims: list[str] = Field(default_factory=list)
    conflicts: list[GoldConflict] = Field(default_factory=list)
    required_sections: list[str] = Field(min_length=1)
    expected_decision: Literal["approve", "reject"] = "approve"


class EvaluationAggregate(StrictModel):
    system: Literal["baseline", "workpilot", "workpilot_single", "workpilot_heterogeneous", "challenger"]
    task_count: int = Field(ge=0)
    completion_rate: float | None = Field(default=None, ge=0, le=1)
    mean_fact_coverage: float | None = Field(default=None, ge=0, le=1)
    mean_unsupported_claim_rate: float | None = Field(default=None, ge=0, le=1)
    mean_citation_validity: float | None = Field(default=None, ge=0, le=1)
    mean_conflict_detection: float | None = Field(default=None, ge=0, le=1)
    format_pass_rate: float | None = Field(default=None, ge=0, le=1)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0)
    model_calls: int
    main_token_share: float | None = Field(default=None, ge=0, le=1)
    subagent_token_share: float | None = Field(default=None, ge=0, le=1)
    p50_latency_ms: float | None = Field(default=None, ge=0)
    p95_latency_ms: float | None = Field(default=None, ge=0)
    total_cost_usd: float | None = Field(default=None, ge=0)
    tokens_per_success: float | None = Field(default=None, ge=0)
    cost_per_success_usd: float | None = Field(default=None, ge=0)


class ModelRole(StrEnum):
    MAIN = "main"
    EXTRACTOR = "achievement_extractor"
    VERIFIER = "fact_verifier"
    BASELINE = "baseline"
    CHALLENGER = "challenger"


class WorkflowStage(StrEnum):
    QUEUED = "queued"
    INGESTING = "ingesting"
    EXTRACTING = "extracting"
    VALIDATING = "validating"
    VERIFYING = "verifying"
    DRAFTING = "drafting"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"
    RECOVERING = "recovering"
    CANCELLED = "cancelled"


class ReportClaim(StrictModel):
    claim_id: str = Field(pattern=r"^claim_[A-Za-z0-9._-]+$")
    section: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=4000)
    fact_ids: list[str] = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)


class ReportDraft(StrictModel):
    claims: list[ReportClaim] = Field(default_factory=list)
    non_factual_notes: dict[str, str] = Field(default_factory=dict)


class ValidationIssue(StrictModel):
    code: str
    message: str
    fact_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ValidationResult(StrictModel):
    passed: bool
    facts: list[Fact]
    issues: list[ValidationIssue] = Field(default_factory=list)


class PromptVersion(StrictModel):
    name: str
    version: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class HarnessVersion(StrictModel):
    name: str
    version: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class Artifact(StrictModel):
    name: str
    relative_path: str
    media_type: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class TaskRecord(StrictModel):
    task_id: str
    report_type: ReportType
    period: str
    status: RunStatus
    stage: WorkflowStage
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    cancel_requested: bool = False


class TaskEvent(StrictModel):
    event_id: str
    task_id: str
    stage: WorkflowStage
    message: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    data: dict[str, Any] = Field(default_factory=dict)

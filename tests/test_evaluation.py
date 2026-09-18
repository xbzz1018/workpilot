from __future__ import annotations

from workpilot.evaluation import _assert_no_secrets, aggregate_results, run_evaluation, score_report
from workpilot.evaluation import _percentile, _summary_markdown
from workpilot.schemas import (
    EvaluationResult,
    Evidence,
    GoldCase,
    GoldFact,
    ImportedPackage,
    Material,
    ReportBundle,
    TaskSpec,
    UsageRecord,
)
from workpilot.settings import Settings


def test_score_success(
    report: ReportBundle,
    task: TaskSpec,
    evidence: Evidence,
    material: Material,
) -> None:
    gold = GoldCase(
        case_id="case",
        facts=[GoldFact(gold_id="g1", statement="done", evidence_ids=[evidence.evidence_id])],
        required_sections=["工作概览", "核心成果", "风险与待确认", "下一步计划"],
    )
    metrics = score_report(report, gold, task, ImportedPackage(materials=[material], evidence=[evidence]))
    assert metrics.task_succeeded
    assert metrics.fact_coverage == 1
    assert metrics.citation_validity == 1


def test_non_required_but_evidence_bound_fact_is_not_unsupported(
    report: ReportBundle,
    task: TaskSpec,
    evidence: Evidence,
    material: Material,
) -> None:
    other = evidence.model_copy(update={"evidence_id": "ev_1111111111111111"})
    extra = report.facts[0].model_copy(update={"fact_id": "fact_extra", "evidence_ids": [other.evidence_id]})
    section = report.sections[1].model_copy(update={
        "fact_ids": [extra.fact_id],
        "evidence_ids": [other.evidence_id],
        "body": f"extra [fact:{extra.fact_id}] [evidence:{other.evidence_id}]",
    })
    updated = report.model_copy(update={
        "facts": [extra],
        "sections": [report.sections[0], section, *report.sections[2:]],
        "markdown": report.markdown.replace(report.sections[1].body, section.body),
    })
    gold = GoldCase(case_id="case", facts=[], required_sections=[item.heading for item in report.sections])
    package = ImportedPackage(materials=[material], evidence=[evidence, other])
    metrics = score_report(updated, gold, task, package)
    assert metrics.unsupported_claim_rate == 0


def test_aggregate_nulls_missing_usage() -> None:
    metrics = __import__("workpilot.schemas", fromlist=["QualityMetrics"]).QualityMetrics(
        fact_coverage=1,
        unsupported_claim_rate=0,
        citation_validity=1,
        format_passed=True,
        task_succeeded=True,
    )
    row = EvaluationResult(
        case_id="c",
        system="workpilot",
        metrics=metrics,
        usage=[UsageRecord(call_id="x", agent_name="main", model="m", duration_ms=1)],
        latency_ms=10,
    )
    aggregate = aggregate_results([row], "workpilot")
    assert aggregate.completion_rate == 1
    assert aggregate.total_tokens is None
    assert aggregate.total_cost_usd is None
    assert aggregate.tokens_per_success is None


def test_aggregate_complete_usage_and_summary() -> None:
    metrics = __import__("workpilot.schemas", fromlist=["QualityMetrics"]).QualityMetrics(
        fact_coverage=0.8,
        unsupported_claim_rate=0.05,
        citation_validity=0.9,
        conflict_detection=1,
        format_passed=True,
        task_succeeded=True,
    )
    rows = []
    for index, latency in enumerate((10.0, 30.0)):
        rows.append(EvaluationResult(
            case_id=f"c{index}",
            system="workpilot",
            metrics=metrics,
            usage=[
                UsageRecord(
                    call_id=f"m{index}", agent_name="main", model="m",
                    input_tokens=60, output_tokens=20, total_tokens=80, cache_read_tokens=10,
                    duration_ms=1, pricing_version="v", estimated_cost_usd=0.01,
                ),
                UsageRecord(
                    call_id=f"s{index}", agent_name="fact_verifier", model="m",
                    input_tokens=15, output_tokens=5, total_tokens=20, cache_read_tokens=0,
                    duration_ms=1, pricing_version="v", estimated_cost_usd=0.005,
                ),
            ],
            latency_ms=latency,
        ))
    aggregate = aggregate_results(rows, "workpilot")
    assert aggregate.total_tokens == 200
    assert aggregate.main_token_share == 0.8
    assert aggregate.subagent_token_share == 0.2
    assert aggregate.p50_latency_ms == 20
    assert aggregate.p95_latency_ms == 29
    assert aggregate.total_cost_usd == 0.03
    assert aggregate.tokens_per_success == 100
    assert "workpilot" in _summary_markdown([aggregate])
    assert aggregate_results([], "baseline").task_count == 0
    assert _percentile([5.0], 0.95) == 5
    assert _percentile([1.0, 2.0, 3.0], 0.5) == 2


def test_real_evaluation_requires_key_before_creating_artifacts(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("WORKPILOT_MAIN_API_KEY", raising=False)
    output = tmp_path / "results"
    with __import__("pytest").raises(RuntimeError, match="WORKPILOT_MAIN_API_KEY"):
        run_evaluation(tmp_path / "evals", output, "dev", "both")
    assert not output.exists()


def _evaluation_env(monkeypatch, tmp_path) -> Settings:
    for name in (
        "WORKPILOT_MAIN_API_KEY",
        "WORKPILOT_EXTRACTOR_API_KEY",
        "WORKPILOT_VERIFIER_API_KEY",
        "WORKPILOT_CHALLENGER_API_KEY",
    ):
        monkeypatch.setenv(name, f"sk-{name.lower()}-secret")
    monkeypatch.setenv("WORKPILOT_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("WORKPILOT_TASK_DB", str(tmp_path / "workspace" / "tasks.sqlite"))
    return Settings.from_env()


def _fake_result(case_dir, workspace_root, system, settings, budget):
    budget.reserve()
    metrics = __import__("workpilot.schemas", fromlist=["QualityMetrics"]).QualityMetrics(
        fact_coverage=1,
        unsupported_claim_rate=0,
        citation_validity=1,
        format_passed=True,
        task_succeeded=True,
    )
    return EvaluationResult(
        case_id=case_dir.name,
        system=system,
        metrics=metrics,
        usage=[UsageRecord(call_id=f"{case_dir.name}-{system}", agent_name="baseline", model="m", duration_ms=1)],
        latency_ms=1,
    )


def test_published_evaluation_rejects_challenger_test_before_artifacts(tmp_path, monkeypatch) -> None:
    _evaluation_env(monkeypatch, tmp_path)
    eval_root = __import__("pathlib").Path(__file__).resolve().parents[1] / "evals-v2"
    output = tmp_path / "results"
    with __import__("pytest").raises(ValueError, match="challenger"):
        run_evaluation(eval_root, output, "test", "challenger", run_id="invalid")
    assert not output.exists()


def test_evaluation_resume_freeze_and_test_gate(tmp_path, monkeypatch) -> None:
    _evaluation_env(monkeypatch, tmp_path)
    module = __import__("workpilot.evaluation", fromlist=["_run_case"])
    eval_root = __import__("pathlib").Path(__file__).resolve().parents[1] / "evals-v2"
    output = tmp_path / "results"
    calls = 0

    def interrupted(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt()
        return _fake_result(*args)

    monkeypatch.setattr(module, "_run_case", interrupted)
    with __import__("pytest").raises(KeyboardInterrupt):
        run_evaluation(eval_root, output, "dev", "baseline", run_id="dev-release")
    run = __import__("json").loads((output / "dev-release" / "run.json").read_text(encoding="utf-8"))
    assert run["status"] == "interrupted"
    assert run["completed_cases"] == 1

    monkeypatch.setattr(module, "_run_case", _fake_result)
    run_evaluation(
        eval_root,
        output,
        "dev",
        "baseline",
        run_id="dev-release",
        resume=True,
        freeze_config=True,
    )
    assert len(list((output / "dev-release" / "cases").glob("*.json"))) == 10
    assert (output / "frozen-config.json").is_file()
    run_evaluation(eval_root, output, "test", "baseline", run_id="test-release")
    assert len(list((output / "test-release" / "cases").glob("*.json"))) == 20


def test_budget_state_and_secret_scan(tmp_path, monkeypatch) -> None:
    settings = _evaluation_env(monkeypatch, tmp_path)
    module = __import__("workpilot.evaluation", fromlist=["_run_case"])
    eval_root = __import__("pathlib").Path(__file__).resolve().parents[1] / "evals-v2"
    output = tmp_path / "results"
    monkeypatch.setattr(module, "_run_case", _fake_result)
    exceeded = __import__("workpilot.audit", fromlist=["ModelCallBudgetExceeded"]).ModelCallBudgetExceeded
    with __import__("pytest").raises(exceeded):
        run_evaluation(eval_root, output, "dev", "baseline", run_id="budget", max_model_calls=2)
    manifest = __import__("json").loads((output / "budget" / "run.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "budget_exhausted"
    assert manifest["completed_cases"] == 2
    leak = output / "budget" / "leak.json"
    leak.write_text(settings.main.api_key, encoding="utf-8")
    with __import__("pytest").raises(RuntimeError, match="secret leakage"):
        _assert_no_secrets(output / "budget", settings)

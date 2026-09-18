"""Deterministic scoring, resumable evaluation, and frozen result publishing."""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal

from workpilot import __version__
from workpilot.agents import build_baseline_agent, build_run_prompt
from workpilot.audit import ModelCallBudget, ModelCallBudgetExceeded, UsageCollector
from workpilot.gate import GateError, validate_report
from workpilot.ingestion import import_package
from workpilot.model import create_models, create_role_model
from workpilot.pipeline import approve_pipeline, run_pipeline
from workpilot.schemas import (
    EvaluationAggregate,
    EvaluationResult,
    GoldCase,
    ImportedPackage,
    ModelRole,
    QualityMetrics,
    ReportBundle,
    TaskSpec,
    UsageRecord,
)
from workpilot.settings import RoleModelSettings, Settings
from workpilot.workspace import TaskWorkspace

EvalSystem = Literal["baseline", "workpilot", "workpilot_single", "workpilot_heterogeneous", "challenger"]
EvalSelection = Literal[
    "baseline",
    "workpilot_single",
    "workpilot_heterogeneous",
    "challenger",
    "both",
    "all_systems",
    "dev_matrix",
    "test_matrix",
]
OFFICIAL_SYSTEMS: tuple[EvalSystem, ...] = (
    "baseline",
    "workpilot_single",
    "workpilot_heterogeneous",
    "challenger",
)
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


def score_report(report: ReportBundle, gold: GoldCase, task: TaskSpec, package: ImportedPackage) -> QualityMetrics:
    required = [fact for fact in gold.facts if fact.required]
    report_supported = [fact for fact in report.facts if fact.status == "supported"]
    covered = sum(
        any(set(actual.evidence_ids) & set(expected.evidence_ids) for actual in report_supported)
        for expected in required
    )
    fact_coverage = covered / len(required) if required else 1.0

    package_evidence = {item.evidence_id for item in package.evidence}
    used_fact_ids = {fact_id for section in report.sections for fact_id in section.fact_ids}
    used_facts = [fact for fact in report.facts if fact.fact_id in used_fact_ids]
    unsupported = sum(
        1 for fact in used_facts
        if fact.status != "supported" or not set(fact.evidence_ids).issubset(package_evidence)
    )
    unsupported += sum(1 for phrase in gold.forbidden_claims if phrase.casefold() in report.markdown.casefold())
    unsupported_claim_rate = unsupported / max(len(used_facts) + len(gold.forbidden_claims), 1)

    facts_by_id = {fact.fact_id: fact for fact in report.facts}
    citations = [(section.fact_ids, evidence_id) for section in report.sections for evidence_id in section.evidence_ids]
    valid_citations = sum(
        1 for fact_ids, evidence_id in citations
        if any(fact_id in facts_by_id and evidence_id in facts_by_id[fact_id].evidence_ids for fact_id in fact_ids)
    )
    citation_validity = valid_citations / len(citations) if citations else (1.0 if not report_supported else 0.0)

    if gold.conflicts:
        detected = sum(
            1 for expected in gold.conflicts
            if any(set(expected.evidence_ids).issubset(set(actual.evidence_ids)) for actual in report.conflicts)
        )
        conflict_detection = detected / len(gold.conflicts)
    else:
        conflict_detection = None

    try:
        validate_report(report, task, package.evidence)
        format_passed = all(section in [item.heading for item in report.sections] for section in gold.required_sections)
    except GateError:
        format_passed = False
    conflict_passed = conflict_detection is None or conflict_detection >= 0.8
    task_succeeded = (
        fact_coverage >= 0.8
        and unsupported_claim_rate <= 0.05
        and citation_validity >= 0.9
        and conflict_passed
        and format_passed
    )
    return QualityMetrics(
        fact_coverage=fact_coverage,
        unsupported_claim_rate=unsupported_claim_rate,
        citation_validity=citation_validity,
        conflict_detection=conflict_detection,
        format_passed=format_passed,
        task_succeeded=task_succeeded,
    )


def aggregate_results(results: Iterable[EvaluationResult], system: EvalSystem) -> EvaluationAggregate:
    rows = [row for row in results if row.system == system]
    if not rows:
        return EvaluationAggregate(system=system, task_count=0, model_calls=0)
    successes = sum(row.metrics.task_succeeded for row in rows)
    all_usage = [usage for row in rows for usage in row.usage]

    def nullable_sum(field: str) -> int | None:
        values = [getattr(usage, field) for usage in all_usage]
        return sum(values) if values and all(value is not None for value in values) else None

    total_tokens = nullable_sum("total_tokens")
    costs = [usage.estimated_cost_usd for usage in all_usage]
    total_cost = sum(costs) if costs and all(value is not None for value in costs) else None
    agent_tokens: dict[str, int] = {}
    if total_tokens is not None:
        for usage in all_usage:
            agent_tokens[usage.agent_name] = agent_tokens.get(usage.agent_name, 0) + int(usage.total_tokens or 0)
    main_tokens = agent_tokens.get("main", 0)
    sub_tokens = agent_tokens.get("achievement_extractor", 0) + agent_tokens.get("fact_verifier", 0)
    conflict_values = [row.metrics.conflict_detection for row in rows if row.metrics.conflict_detection is not None]
    latencies = sorted(row.latency_ms for row in rows)
    return EvaluationAggregate(
        system=system,
        task_count=len(rows),
        completion_rate=successes / len(rows),
        mean_fact_coverage=statistics.fmean(row.metrics.fact_coverage for row in rows),
        mean_unsupported_claim_rate=statistics.fmean(row.metrics.unsupported_claim_rate for row in rows),
        mean_citation_validity=statistics.fmean(row.metrics.citation_validity for row in rows),
        mean_conflict_detection=statistics.fmean(conflict_values) if conflict_values else None,
        format_pass_rate=statistics.fmean(float(row.metrics.format_passed) for row in rows),
        input_tokens=nullable_sum("input_tokens"),
        output_tokens=nullable_sum("output_tokens"),
        total_tokens=total_tokens,
        cache_read_tokens=nullable_sum("cache_read_tokens"),
        model_calls=len(all_usage),
        main_token_share=(main_tokens / total_tokens) if total_tokens else None,
        subagent_token_share=(sub_tokens / total_tokens) if total_tokens else None,
        p50_latency_ms=statistics.median(latencies),
        p95_latency_ms=_percentile(latencies, 0.95),
        total_cost_usd=total_cost,
        tokens_per_success=(total_tokens / successes) if total_tokens is not None and successes else None,
        cost_per_success_usd=(total_cost / successes) if total_cost is not None and successes else None,
    )


def run_evaluation(
    eval_root: str | Path,
    output_root: str | Path,
    split: Literal["dev", "test", "all"],
    system: EvalSelection,
    *,
    run_id: str | None = None,
    resume: bool = False,
    max_model_calls: int = 400,
    freeze_config: bool = False,
    frozen_config_path: str | Path | None = None,
    case_ids: Iterable[str] | None = None,
) -> Path:
    settings = Settings.from_env()
    eval_root = Path(eval_root).resolve()
    output_root = Path(output_root).resolve()
    splits, systems = _selection(split, system)
    _validate_dataset(eval_root, splits)
    selected_case_ids = sorted(set(case_ids or []))
    case_map = {item: _case_dirs(eval_root, item, selected_case_ids) for item in splits}
    if selected_case_ids:
        found = {path.name for paths in case_map.values() for path in paths}
        missing = set(selected_case_ids) - found
        if missing:
            raise ValueError(f"unknown evaluation case IDs: {sorted(missing)}")
    source_manifest = _source_manifest(eval_root)
    config_sha256 = _sha256_json(source_manifest)
    frozen_path = Path(frozen_config_path).resolve() if frozen_config_path else output_root / "frozen-config.json"
    if "test" in splits:
        _validate_frozen_config(frozen_path, config_sha256)

    run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError("run_id must contain only letters, digits, dot, underscore, or hyphen")
    run_dir = output_root / run_id
    manifest_path = run_dir / "run.json"
    now = _utc_now()
    if resume:
        if not manifest_path.exists():
            raise FileNotFoundError(f"evaluation run does not exist: {run_id}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["split"] != split or manifest["systems"] != systems:
            raise ValueError("resume split/system does not match the existing run")
        if manifest.get("case_ids", []) != selected_case_ids:
            raise ValueError("resume case selection does not match the existing run")
        if manifest["config_sha256"] != config_sha256:
            raise ValueError("source, Prompt, Skill, or Profile changed since the run started")
    else:
        run_dir.mkdir(parents=True, exist_ok=False)
        manifest = {
            "run_id": run_id,
            "version": __version__,
            "status": "running",
            "started_at": now,
            "updated_at": now,
            "split": split,
            "systems": systems,
            "max_model_calls": max_model_calls,
            "model_calls": 0,
            "planned_cases": sum(len(case_map[item]) for item in splits) * len(systems),
            "completed_cases": 0,
            "failed_cases": 0,
            "dataset_root": str(eval_root),
            "dataset_manifest_sha256": _file_sha256(eval_root / "test-manifest.json"),
            "config_sha256": config_sha256,
            "models": {role.value: settings.for_role(role).model for role in ModelRole},
            "key_aliases": {role.value: settings.for_role(role).key_alias for role in ModelRole},
            "case_ids": selected_case_ids,
        }
        _atomic_json(manifest_path, manifest)
        _atomic_json(run_dir / "source-manifest.json", source_manifest)

    existing = _load_results(run_dir / "cases")
    global_call_ids = _global_call_ids(output_root)
    global_used = len(global_call_ids) + _smoke_call_count(output_root / "model-smoke.json")
    budget = ModelCallBudget(max_model_calls, global_used)
    results = list(existing)
    completed_keys = {(item.case_id, item.system) for item in existing}

    try:
        for split_name in splits:
            for case_dir in case_map[split_name]:
                for system_name in systems:
                    key = (case_dir.name, system_name)
                    if key in completed_keys:
                        continue
                    result = _run_case(
                        case_dir,
                        run_dir / "workspaces" / split_name,
                        system_name,
                        settings,
                        budget,
                    )
                    results.append(result)
                    completed_keys.add(key)
                    target = run_dir / "cases" / f"{split_name}.{case_dir.name}.{system_name}.json"
                    _atomic_text(target, result.model_dump_json(indent=2) + "\n")
                    _publish_progress(run_dir, manifest, results, systems, budget.used)
    except ModelCallBudgetExceeded:
        manifest["status"] = "budget_exhausted"
        _publish_progress(run_dir, manifest, results, systems, budget.used)
        _assert_no_secrets(run_dir, settings)
        raise
    except BaseException:
        manifest["status"] = "interrupted"
        _publish_progress(run_dir, manifest, results, systems, budget.used)
        _assert_no_secrets(run_dir, settings)
        raise

    manifest["status"] = "completed"
    _publish_progress(run_dir, manifest, results, systems, budget.used)
    if freeze_config:
        if splits != ["dev"]:
            raise ValueError("only a completed Dev run can freeze evaluation configuration")
        frozen = {
            "version": __version__,
            "dev_run_id": run_id,
            "dev_case_ids": selected_case_ids or "all",
            "frozen_at": _utc_now(),
            "config_sha256": config_sha256,
            "dataset_manifest_sha256": manifest["dataset_manifest_sha256"],
            "models": manifest["models"],
        }
        _atomic_json(frozen_path, frozen)
    _assert_no_secrets(run_dir, settings)
    return run_dir


def _run_case(
    case_dir: Path,
    workspace_root: Path,
    system: EvalSystem,
    settings: Settings,
    budget: ModelCallBudget,
) -> EvaluationResult:
    task = TaskSpec.model_validate_json((case_dir / "task.json").read_text(encoding="utf-8"))
    gold = GoldCase.model_validate_json((case_dir / "gold.json").read_text(encoding="utf-8"))
    package = import_package(case_dir / "input")
    started = time.perf_counter()
    usage: list[UsageRecord] = []
    collector: UsageCollector | None = None
    task_workspace: TaskWorkspace | None = None
    errors: list[str] = []
    report: ReportBundle | None = None
    try:
        if system == "baseline":
            collector = UsageCollector()
            model = create_role_model(settings.baseline, settings.base_url)
            agent = build_baseline_agent(
                collector,
                model,
                model_settings=settings.baseline,
                budget=budget,
            )
            result = agent.invoke({"messages": [{"role": "user", "content": build_run_prompt(task, package)}]})
            report = ReportBundle.model_validate(result.get("structured_response"))
            usage = collector.records
        elif system == "workpilot":
            raise ValueError("legacy workpilot evaluation is not part of the v2 published matrix")
        else:
            role_models = create_models(settings)
            audit_settings: dict[ModelRole, RoleModelSettings] = {
                role: settings.for_role(role) for role in ModelRole
            }
            if system == "workpilot_single":
                main = create_role_model(settings.main, settings.base_url)
                role_models = {role: main for role in ModelRole}
                audit_settings = {role: settings.main for role in ModelRole}
            elif system == "challenger":
                challenger = create_role_model(settings.challenger, settings.base_url)
                role_models = {role: challenger for role in ModelRole}
                audit_settings = {role: settings.challenger for role in ModelRole}
            task_workspace = TaskWorkspace(workspace_root / system, task.task_id)
            if not task_workspace.exists("task.json"):
                task_workspace.write_task(task)
                task_workspace.stage_input(case_dir / "input", package)
            report = run_pipeline(
                task_workspace,
                task,
                package,
                settings=settings,
                models=role_models,
                audit_settings=audit_settings,
                budget=budget,
            )
            approve_pipeline(task_workspace, "approve")
            usage = _read_workspace_usage(task_workspace)
        metrics = score_report(report, gold, task, package)
    except ModelCallBudgetExceeded:
        raise
    except Exception as exc:
        errors.append(_safe_error(exc, settings))
        metrics = QualityMetrics(
            fact_coverage=0,
            unsupported_claim_rate=1,
            citation_validity=0,
            conflict_detection=0 if gold.conflicts else None,
            format_passed=False,
            task_succeeded=False,
        )
    finally:
        if collector is not None:
            usage = collector.records
        elif task_workspace is not None:
            usage = _read_workspace_usage(task_workspace)
    return EvaluationResult(
        case_id=case_dir.name,
        system=system,
        metrics=metrics,
        usage=usage,
        latency_ms=(time.perf_counter() - started) * 1000,
        errors=errors,
        report=report,
    )


def _selection(split: str, selection: str) -> tuple[list[str], list[EvalSystem]]:
    splits = ["dev", "test"] if split == "all" else [split]
    if selection in {"all_systems", "dev_matrix"}:
        if splits != ["dev"]:
            raise ValueError("challenger and the Dev matrix are restricted to split=dev")
        systems: list[EvalSystem] = list(OFFICIAL_SYSTEMS)
    elif selection == "test_matrix":
        if splits != ["test"]:
            raise ValueError("test_matrix requires split=test")
        systems = ["baseline", "workpilot_single", "workpilot_heterogeneous"]
    elif selection == "both":
        systems = ["baseline", "workpilot_heterogeneous"]
    else:
        systems = [selection]  # type: ignore[list-item]
    if "challenger" in systems and splits != ["dev"]:
        raise ValueError("challenger is restricted to split=dev")
    return splits, systems


def _validate_dataset(eval_root: Path, splits: list[str]) -> None:
    manifest_path = eval_root / "test-manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing frozen dataset manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("version") != "workpilot-test-v2":
        raise ValueError("real evaluation requires the workpilot-test-v2 dataset")
    for relative, expected in manifest.get("files", {}).items():
        path = eval_root / relative
        if not path.is_file() or _file_sha256(path) != expected:
            raise ValueError(f"frozen Test manifest mismatch: {relative}")
    for split in splits:
        if not (eval_root / split).is_dir():
            raise FileNotFoundError(f"missing evaluation split: {split}")


def _validate_frozen_config(path: Path, config_sha256: str) -> None:
    if not path.is_file():
        raise FileNotFoundError("frozen-config.json is required before running Test")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("config_sha256") != config_sha256:
        raise ValueError("current source/Prompt/Skill/Profile does not match frozen Dev configuration")


def _source_manifest(eval_root: Path) -> dict:
    project_root = Path(__file__).resolve().parents[2]
    candidates = [
        *sorted((project_root / "src" / "workpilot").rglob("*.py")),
        *sorted((project_root / "src" / "workpilot" / "prompts").rglob("*.md")),
        *sorted((project_root / "src" / "workpilot" / "prompts").rglob("*.json")),
        *sorted((project_root / "src" / "workpilot" / "skills").rglob("*")),
        project_root / "pyproject.toml",
        project_root / "requirements.lock",
        eval_root / "test-manifest.json",
    ]
    files = {
        path.relative_to(project_root).as_posix(): _file_sha256(path)
        for path in candidates
        if path.is_file()
    }
    return {"algorithm": "sha256", "files": dict(sorted(files.items()))}


def _publish_progress(
    run_dir: Path,
    manifest: dict,
    results: list[EvaluationResult],
    systems: list[EvalSystem],
    model_calls: int,
) -> None:
    failures = [result for result in results if result.errors or not result.metrics.task_succeeded]
    aggregates = [aggregate_results(results, item) for item in systems]
    run_call_ids = {usage.call_id for result in results for usage in result.usage}
    run_call_ids.update(_workspace_call_ids(run_dir / "workspaces"))
    manifest.update(
        updated_at=_utc_now(),
        model_calls=len(run_call_ids),
        global_model_calls=model_calls,
        completed_cases=len(results),
        failed_cases=len(failures),
    )
    _atomic_json(run_dir / "run.json", manifest)
    _atomic_json(run_dir / "aggregate.json", [item.model_dump(mode="json") for item in aggregates])
    _atomic_text(run_dir / "summary.md", _summary_markdown(aggregates, failures=len(failures)))
    failure_rows = []
    for result in failures:
        name = f"{result.case_id}.{result.system}.json"
        _atomic_text(run_dir / "failures" / name, result.model_dump_json(indent=2) + "\n")
        failure_rows.append({"case_id": result.case_id, "system": result.system, "file": name, "errors": result.errors})
    _atomic_json(run_dir / "failures" / "index.json", failure_rows)


def _load_results(cases_dir: Path) -> list[EvaluationResult]:
    if not cases_dir.exists():
        return []
    return [
        EvaluationResult.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(cases_dir.glob("*.json"))
    ]


def _workspace_call_ids(root: Path) -> set[str]:
    ids: set[str] = set()
    if not root.exists():
        return ids
    for path in root.rglob("usage.json"):
        try:
            ids.update(str(item["call_id"]) for item in json.loads(path.read_text(encoding="utf-8")))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return ids


def _global_call_ids(output_root: Path) -> set[str]:
    ids = _workspace_call_ids(output_root)
    for path in output_root.glob("*/cases/*.json"):
        try:
            result = EvaluationResult.model_validate_json(path.read_text(encoding="utf-8"))
            ids.update(item.call_id for item in result.usage)
        except (ValueError, OSError):
            continue
    return ids


def _smoke_call_count(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError, json.JSONDecodeError):
        return 0
    return sum(4 for row in rows if isinstance(row, dict) and row.get("ok"))


def _read_workspace_usage(workspace: TaskWorkspace) -> list[UsageRecord]:
    if not workspace.exists("usage.json"):
        return []
    return [UsageRecord.model_validate(item) for item in workspace.read_json("usage.json")]


def _case_dirs(eval_root: Path, split: str, case_ids: list[str] | None = None) -> list[Path]:
    selected = set(case_ids or [])
    return sorted(
        path for path in (eval_root / split).iterdir()
        if path.is_dir() and (not selected or path.name in selected)
    )


def _safe_error(exc: Exception, settings: Settings) -> str:
    text = f"{type(exc).__name__}: {exc}"
    for role in ModelRole:
        secret = settings.for_role(role).api_key
        if secret:
            text = text.replace(secret, "<redacted>")
    return re.sub(r"(?i)sk-[A-Za-z0-9_-]{8,}", "<redacted>", text)


def _assert_no_secrets(run_dir: Path, settings: Settings) -> None:
    secrets = {settings.for_role(role).api_key for role in ModelRole}
    secrets.discard("")
    for path in run_dir.rglob("*"):
        if not path.is_file() or "workspaces" in path.parts:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if any(secret in text for secret in secrets):
            raise RuntimeError(f"secret leakage detected in evaluation artifact: {path.name}")


def _atomic_json(path: Path, value: object) -> None:
    _atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_json(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _percentile(values: list[float], percentile: float) -> float:
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def _summary_markdown(aggregates: list[EvaluationAggregate], *, failures: int = 0) -> str:
    lines = [
        "# WorkPilot Evaluation",
        "",
        "Costs remain `null` when usage or the selected model price is unavailable.",
        f"Failed or below-threshold case/system results: **{failures}**.",
        "",
        "| System | Tasks | Completion | Fact coverage | Unsupported | Citation validity | Conflict detection | Format pass | Calls | P50 ms | P95 ms | Total cost USD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in aggregates:
        values = item.model_dump()
        lines.append(
            f"| {item.system} | {item.task_count} | {_pct(item.completion_rate)} | {_pct(item.mean_fact_coverage)} | "
            f"{_pct(item.mean_unsupported_claim_rate)} | {_pct(item.mean_citation_validity)} | "
            f"{_pct(item.mean_conflict_detection)} | {_pct(item.format_pass_rate)} | {item.model_calls} | "
            f"{_num(item.p50_latency_ms)} | {_num(item.p95_latency_ms)} | {_num(values['total_cost_usd'])} |"
        )
    return "\n".join(lines) + "\n"


def _pct(value: float | None) -> str:
    return "null" if value is None else f"{value:.1%}"


def _num(value: float | None) -> str:
    return "null" if value is None else f"{value:.6f}"

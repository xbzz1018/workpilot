"""Command-line interface for WorkPilot runs, recovery, history, and evaluation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from workpilot.evaluation import run_evaluation
from workpilot.schemas import RunStatus
from workpilot.workflow import get_history, resume_run, start_run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workpilot")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="create a report draft and pause for approval")
    run.add_argument("--input", required=True, type=Path)
    run.add_argument("--type", required=True, choices=["work_summary", "performance_review"])
    run.add_argument("--period", required=True)
    run.add_argument("--workspace", required=True, type=Path)
    run.add_argument("--thread-id")

    resume = commands.add_parser("resume", help="approve, edit, or reject a waiting draft")
    resume.add_argument("--workspace", required=True, type=Path)
    resume.add_argument("--thread-id", required=True)
    resume.add_argument("--decision", required=True, choices=["approve", "edit", "reject"])
    resume.add_argument("--edited-file", type=Path)
    resume.add_argument("--message")

    history = commands.add_parser("history", help="show checkpoint history")
    history.add_argument("--workspace", required=True, type=Path)
    history.add_argument("--thread-id", required=True)

    evaluate = commands.add_parser("eval", help="run the real-model benchmark")
    evaluate.add_argument("--split", required=True, choices=["dev", "test", "all"])
    evaluate.add_argument(
        "--system",
        required=True,
        choices=["baseline", "workpilot_single", "workpilot_heterogeneous", "challenger", "both", "all_systems", "dev_matrix", "test_matrix"],
    )
    project_root = Path(__file__).resolve().parents[2]
    evaluate.add_argument("--eval-root", type=Path, default=project_root / "evals-v2")
    evaluate.add_argument("--output", type=Path, default=project_root / "results")
    evaluate.add_argument("--run-id")
    evaluate.add_argument("--resume", action="store_true")
    evaluate.add_argument("--max-model-calls", type=int, default=400)
    evaluate.add_argument("--freeze-config", action="store_true")
    evaluate.add_argument("--frozen-config", type=Path)
    evaluate.add_argument("--case", dest="case_ids", action="append", default=[])
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run":
            outcome = start_run(args.input, args.workspace, args.type, args.period, args.thread_id)
            print(outcome.model_dump_json(indent=2))
            return _status_code(outcome.status)
        if args.command == "resume":
            outcome = resume_run(
                args.workspace,
                args.thread_id,
                args.decision,
                edited_file=args.edited_file,
                message=args.message,
            )
            print(outcome.model_dump_json(indent=2))
            return _status_code(outcome.status)
        if args.command == "history":
            print(json.dumps(get_history(args.workspace, args.thread_id), ensure_ascii=False, indent=2))
            return 0
        output = run_evaluation(
            args.eval_root,
            args.output,
            args.split,
            args.system,
            run_id=args.run_id,
            resume=args.resume,
            max_model_calls=args.max_model_calls,
            freeze_config=args.freeze_config,
            frozen_config_path=args.frozen_config,
            case_ids=args.case_ids,
        )
        print(output)
        return 0
    except Exception as exc:
        print(f"workpilot: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


def _status_code(status: RunStatus) -> int:
    if status is RunStatus.WAITING_APPROVAL:
        return 2
    if status is RunStatus.REJECTED:
        return 3
    return 0 if status is RunStatus.COMPLETED else 1


if __name__ == "__main__":
    raise SystemExit(main())

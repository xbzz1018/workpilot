from __future__ import annotations

import pytest

from workpilot.cli import _status_code, build_parser
from workpilot.model import create_model
from workpilot.schemas import RunStatus


def test_model_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("WORKPILOT_MAIN_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="WORKPILOT_MAIN_API_KEY"):
        create_model()


def test_model_uses_locked_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKPILOT_MAIN_API_KEY", "test-key")
    monkeypatch.delenv("WORKPILOT_MODEL", raising=False)
    monkeypatch.delenv("WORKPILOT_MAIN_MODEL", raising=False)
    model = create_model()
    assert model.model_name == "deepseek-v4-pro"
    assert model.temperature == 0


def test_cli_contract_and_exit_codes() -> None:
    args = build_parser().parse_args([
        "run", "--input", "input", "--type", "work_summary",
        "--period", "2026-W35", "--workspace", "workspace",
    ])
    assert args.command == "run"
    assert _status_code(RunStatus.COMPLETED) == 0
    assert _status_code(RunStatus.WAITING_APPROVAL) == 2
    assert _status_code(RunStatus.REJECTED) == 3
    assert _status_code(RunStatus.FAILED) == 1

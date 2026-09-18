from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

from workpilot.schemas import GoldCase, TaskSpec


def test_dataset_counts_and_frozen_manifest() -> None:
    root = Path(__file__).resolve().parents[1] / "evals"
    cases = [path for split in ("dev", "test") for path in (root / split).iterdir() if path.is_dir()]
    assert len(cases) == 30
    assert len([path for path in (root / "dev").iterdir() if path.is_dir()]) == 10
    assert len([path for path in (root / "test").iterdir() if path.is_dir()]) == 20
    scenarios = Counter(path.name.rsplit("-", 1)[-1] for path in cases)
    assert scenarios == {"regular": 8, "dedupe": 6, "conflict": 6, "missing": 5, "long": 5}
    report_types = Counter(TaskSpec.model_validate_json((path / "task.json").read_text(encoding="utf-8")).report_type for path in cases)
    assert sorted(report_types.values()) == [15, 15]
    for path in cases:
        GoldCase.model_validate_json((path / "gold.json").read_text(encoding="utf-8"))

    manifest = json.loads((root / "test-manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["files"]) == 100
    for relative, expected in manifest["files"].items():
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == expected


def test_v2_dataset_is_separate_and_frozen() -> None:
    root = Path(__file__).resolve().parents[1] / "evals-v2"
    assert len([path for path in (root / "dev").iterdir() if path.is_dir()]) == 10
    assert len([path for path in (root / "test").iterdir() if path.is_dir()]) == 20
    manifest = json.loads((root / "test-manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "workpilot-test-v2"
    assert len(manifest["files"]) == 100
    for relative, expected in manifest["files"].items():
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == expected

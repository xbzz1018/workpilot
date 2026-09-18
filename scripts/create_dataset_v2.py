"""Create the immutable v2 benchmark snapshot without altering v1."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    source = root / "evals"
    target = root / "evals-v2"
    if target.exists():
        raise SystemExit("evals-v2 already exists; never overwrite a frozen dataset")
    target.mkdir()
    for split in ("dev", "test"):
        shutil.copytree(source / split, target / split)
    (target / "README.md").write_text(
        "# WorkPilot Evaluation Dataset v2\n\n"
        "v2 preserves the v1 source cases and split while evaluating atomic Fact fields, ReportClaim rendering, "
        "single-model WorkPilot, heterogeneous WorkPilot, and a Dev-only challenger. The v1 snapshot remains unchanged.\n",
        encoding="utf-8",
    )
    entries = {}
    for path in sorted((target / "test").rglob("*")):
        if path.is_file():
            entries[path.relative_to(target).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {"version": "workpilot-test-v2", "frozen_at": "2026-08-31T00:00:00Z", "algorithm": "sha256", "files": entries}
    (target / "test-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()

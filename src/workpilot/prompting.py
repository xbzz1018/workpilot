"""Versioned prompt resource loading."""

from __future__ import annotations

import hashlib
import json
from importlib.resources import files

from workpilot.schemas import PromptVersion


def load_prompt(name: str) -> tuple[str, PromptVersion]:
    root = files("workpilot").joinpath("prompts")
    text = root.joinpath(f"{name}.md").read_text(encoding="utf-8")
    versions = json.loads(root.joinpath("versions.json").read_text(encoding="utf-8"))
    return text, PromptVersion(name=name, version=versions[name], sha256=hashlib.sha256(text.encode("utf-8")).hexdigest())


def prompt_versions() -> list[PromptVersion]:
    return [load_prompt(name)[1] for name in ("main", "extractor", "verifier", "baseline")]


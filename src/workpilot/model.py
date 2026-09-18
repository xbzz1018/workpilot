"""OpenAI-compatible role model factory for the VibeAPI gateway."""

from __future__ import annotations

import os
from typing import Any

from langchain_openai import ChatOpenAI

from workpilot.schemas import ModelRole
from workpilot.settings import RoleModelSettings, Settings


def create_role_model(config: RoleModelSettings, base_url: str) -> ChatOpenAI:
    return ChatOpenAI(
        model=config.model,
        api_key=config.api_key,
        base_url=base_url,
        temperature=0.0,
        timeout=config.timeout_seconds,
        max_retries=2,
    )


def create_models(settings: Settings | None = None, *, allow_placeholder: bool = False) -> dict[ModelRole, ChatOpenAI]:
    settings = settings or Settings.from_env(allow_placeholder=allow_placeholder)
    return {role: create_role_model(settings.for_role(role), settings.base_url) for role in ModelRole}


def create_model(*, allow_placeholder: bool = False) -> ChatOpenAI:
    """Backward-compatible main model factory used by the v1 CLI."""
    key = os.getenv("WORKPILOT_MAIN_API_KEY") or os.getenv("DEEPSEEK_API_KEY")
    if not key:
        if not allow_placeholder:
            raise RuntimeError("WORKPILOT_MAIN_API_KEY is required for the main model")
        key = "not-used"
    config = RoleModelSettings(
        role=ModelRole.MAIN,
        model=os.getenv("WORKPILOT_MAIN_MODEL", os.getenv("WORKPILOT_MODEL", "deepseek-v4-pro")),
        api_key=key,
        key_alias="workpilot_main_api_key",
        timeout_seconds=float(os.getenv("WORKPILOT_MAIN_MODEL_TIMEOUT", "180")),
    )
    return create_role_model(config, os.getenv("WORKPILOT_BASE_URL", "https://www.vibeapi.cn/v1"))


def model_identity(model: Any) -> str:
    return getattr(model, "model_name", None) or getattr(model, "model", None) or "unknown"

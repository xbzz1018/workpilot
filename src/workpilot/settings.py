"""Environment-only configuration. Secret values are never serialized."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from workpilot.schemas import ModelRole


@dataclass(frozen=True)
class RoleModelSettings:
    role: ModelRole
    model: str
    api_key: str
    key_alias: str
    timeout_seconds: float


@dataclass(frozen=True)
class Settings:
    base_url: str
    workspace_root: Path
    task_db_path: Path
    retention_days: int
    max_active_tasks: int
    main: RoleModelSettings
    extractor: RoleModelSettings
    verifier: RoleModelSettings
    baseline: RoleModelSettings
    challenger: RoleModelSettings

    @classmethod
    def from_env(cls, *, allow_placeholder: bool = False) -> Settings:
        placeholder = "not-used" if allow_placeholder else ""

        def role(model_role: ModelRole, model_var: str, key_var: str, default_model: str, timeout: float) -> RoleModelSettings:
            key = os.getenv(key_var, placeholder)
            if not key:
                raise RuntimeError(f"{key_var} is required for role {model_role.value}")
            return RoleModelSettings(
                role=model_role,
                model=os.getenv(model_var, default_model),
                api_key=key,
                key_alias=key_var.lower(),
                timeout_seconds=float(os.getenv(f"{model_var}_TIMEOUT", timeout)),
            )

        workspace = Path(os.getenv("WORKPILOT_WORKSPACE", "workspace")).expanduser().resolve()
        return cls(
            base_url=os.getenv("WORKPILOT_BASE_URL", "https://api.example.invalid/v1").rstrip("/"),
            workspace_root=workspace,
            task_db_path=Path(os.getenv("WORKPILOT_TASK_DB", str(workspace / "workpilot.sqlite"))).expanduser().resolve(),
            retention_days=int(os.getenv("WORKPILOT_RETENTION_DAYS", "30")),
            max_active_tasks=int(os.getenv("WORKPILOT_MAX_ACTIVE_TASKS", "3")),
            main=role(ModelRole.MAIN, "WORKPILOT_MAIN_MODEL", "WORKPILOT_MAIN_API_KEY", "deepseek-v4-pro", 180),
            extractor=role(ModelRole.EXTRACTOR, "WORKPILOT_EXTRACTOR_MODEL", "WORKPILOT_EXTRACTOR_API_KEY", "deepseek-v4-flash", 90),
            verifier=role(ModelRole.VERIFIER, "WORKPILOT_VERIFIER_MODEL", "WORKPILOT_VERIFIER_API_KEY", "glm-5.3", 120),
            baseline=role(ModelRole.BASELINE, "WORKPILOT_BASELINE_MODEL", "WORKPILOT_MAIN_API_KEY", "deepseek-v4-pro", 180),
            challenger=role(ModelRole.CHALLENGER, "WORKPILOT_CHALLENGER_MODEL", "WORKPILOT_CHALLENGER_API_KEY", "kimi-k3", 180),
        )

    def for_role(self, role: ModelRole) -> RoleModelSettings:
        return {
            ModelRole.MAIN: self.main,
            ModelRole.EXTRACTOR: self.extractor,
            ModelRole.VERIFIER: self.verifier,
            ModelRole.BASELINE: self.baseline,
            ModelRole.CHALLENGER: self.challenger,
        }[role]

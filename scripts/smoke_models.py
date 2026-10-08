"""Real VibeAPI capability gate. Never prints or serializes API keys."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import replace
from pathlib import Path

import httpx
from dotenv import load_dotenv, set_key
from langchain_core.tools import tool
from pydantic import BaseModel

from workpilot.model import create_role_model
from workpilot.schemas import ModelRole
from workpilot.settings import RoleModelSettings, Settings


ROLE_MODEL_ENV = {
    ModelRole.MAIN: "WORKPILOT_MAIN_MODEL",
    ModelRole.EXTRACTOR: "WORKPILOT_EXTRACTOR_MODEL",
    ModelRole.VERIFIER: "WORKPILOT_VERIFIER_MODEL",
    ModelRole.CHALLENGER: "WORKPILOT_CHALLENGER_MODEL",
}
SMOKE_ROLES = tuple(ROLE_MODEL_ENV)
FAMILY_TOKENS = {
    ModelRole.MAIN: ("deepseek", "v4", "pro"),
    ModelRole.EXTRACTOR: ("deepseek", "v4", "flash"),
    ModelRole.VERIFIER: ("glm", "5", "3"),
    ModelRole.CHALLENGER: ("kimi", "k3"),
}


class SmokeResponse(BaseModel):
    status: str
    value: int


@tool
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


def resolve_model(config: RoleModelSettings, role: ModelRole, model_ids: set[str]) -> RoleModelSettings:
    if config.model in model_ids:
        return config
    normalized = {model_id: re.sub(r"[^a-z0-9]+", "", model_id.casefold()) for model_id in model_ids}
    tokens = FAMILY_TOKENS[role]
    candidates = [
        model_id for model_id, compact in normalized.items()
        if all(re.sub(r"[^a-z0-9]+", "", token.casefold()) in compact for token in tokens)
    ]
    if len(candidates) != 1:
        raise RuntimeError(f"configured model is not visible and exact family match is ambiguous: {config.model}")
    return replace(config, model=candidates[0])


def smoke_role(settings: Settings, role: ModelRole) -> tuple[dict, RoleModelSettings]:
    original = settings.for_role(role)
    headers = {"Authorization": f"Bearer {original.api_key}"}
    response = httpx.get(f"{settings.base_url}/models", headers=headers, timeout=30)
    response.raise_for_status()
    model_ids = {str(item.get("id")) for item in response.json().get("data", []) if item.get("id")}
    config = resolve_model(original, role, model_ids)

    model = create_role_model(config, settings.base_url)
    ordinary = model.invoke("Reply with exactly: pong")
    tool_message = model.bind_tools([add], tool_choice="required").invoke("Use add to calculate 2+3")
    structured = model.with_structured_output(SmokeResponse, method="function_calling").invoke(
        "Return status=ok and value=5"
    )
    stream_text = "".join(str(chunk.content or "") for chunk in model.stream("Reply with exactly: stream-ok"))
    usage = ordinary.usage_metadata or {}
    metadata = ordinary.response_metadata or {}
    actual_model = metadata.get("model_name") or metadata.get("model") or config.model

    error_response = httpx.post(
        f"{settings.base_url}/chat/completions",
        headers={**headers, "Content-Type": "application/json"},
        json={"model": f"{config.model}-workpilot-invalid", "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
        timeout=30,
    )
    error_json = None
    try:
        error_json = error_response.json()
    except ValueError:
        pass
    error_shape = sorted(error_json) if isinstance(error_json, dict) else []
    if error_response.is_success:
        raise RuntimeError("relay accepted an intentionally invalid model id")

    result = {
        "role": role.value,
        "key_alias": config.key_alias,
        "requested_model": config.model,
        "actual_model": actual_model,
        "ordinary_nonempty": bool(ordinary.content),
        "tool_call": bool(tool_message.tool_calls),
        "structured": structured.model_dump(),
        "stream_nonempty": bool(stream_text),
        "usage_present": bool(usage),
        "usage_fields": sorted(usage),
        "error_status": error_response.status_code,
        "error_shape": error_shape,
    }
    return result, config


def _safe_error(exc: Exception, secret: str) -> str:
    text = str(exc).replace(secret, "<redacted>")
    return re.sub(r"(?i)sk-[A-Za-z0-9_-]{8,}", "<redacted>", text)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["all", *(role.value for role in SMOKE_ROLES)], default="all")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--update-env", action="store_true")
    args = parser.parse_args()
    load_dotenv(args.env_file)
    try:
        settings = Settings.from_env()
    except Exception as exc:
        print(json.dumps({"ok": False, "error_type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False, indent=2))
        return 1
    roles = list(SMOKE_ROLES) if args.role == "all" else [ModelRole(args.role)]
    results = []
    resolved: dict[ModelRole, RoleModelSettings] = {}
    for role in roles:
        config = settings.for_role(role)
        try:
            result, resolved_config = smoke_role(settings, role)
            results.append({"ok": True, **result})
            resolved[role] = resolved_config
        except Exception as exc:
            results.append({
                "ok": False,
                "role": role.value,
                "key_alias": config.key_alias,
                "error_type": type(exc).__name__,
                "message": _safe_error(exc, config.api_key),
            })
    if args.update_env:
        for role, config in resolved.items():
            set_key(str(args.env_file), ROLE_MODEL_ENV[role], config.model, quote_mode="always")
        set_key(str(args.env_file), "WORKPILOT_BASELINE_MODEL", resolved.get(ModelRole.MAIN, settings.main).model, quote_mode="always")
    output = json.dumps(results, ensure_ascii=False, indent=2)
    print(output)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    return 0 if all(item["ok"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

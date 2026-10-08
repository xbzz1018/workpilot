"""Create a gitignored local environment without exposing secrets to shell output."""

from __future__ import annotations

import argparse
import re
import secrets
import subprocess
from pathlib import Path


DEFAULT_ATTACHMENT = Path.home() / "Desktop" / "项目3.txt"


def extract_keys(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8-sig")
    keys = re.findall(r"(?i)sk-[A-Za-z0-9_-]{8,}", text)
    if len(keys) != 4 or len(set(keys)) != 4:
        raise ValueError("attachment must contain exactly four distinct sk-* keys")
    return keys


def caddy_hash(password: str) -> str:
    result = subprocess.run(
        ["docker", "run", "--rm", "caddy:2.10-alpine", "caddy", "hash-password", "--plaintext", password],
        check=True,
        capture_output=True,
        text=True,
    )
    value = result.stdout.strip()
    if not value.startswith("$"):
        raise RuntimeError("Caddy did not return a password hash")
    return value


def quote(value: str) -> str:
    if "'" in value or "\n" in value or "\r" in value:
        raise ValueError("unsupported environment value")
    return f"'{value}'"


def render_env(keys: list[str], password: str, password_hash: str) -> str:
    rows = [
        "WORKPILOT_BASE_URL=https://www.vibeapi.cn/v1",
        f"WORKPILOT_MAIN_API_KEY={quote(keys[0])}",
        f"WORKPILOT_EXTRACTOR_API_KEY={quote(keys[1])}",
        f"WORKPILOT_VERIFIER_API_KEY={quote(keys[2])}",
        f"WORKPILOT_CHALLENGER_API_KEY={quote(keys[3])}",
        "WORKPILOT_MAIN_MODEL=deepseek-v4-pro",
        "WORKPILOT_EXTRACTOR_MODEL=deepseek-v4-flash",
        "WORKPILOT_VERIFIER_MODEL=glm-5.3",
        "WORKPILOT_BASELINE_MODEL=deepseek-v4-pro",
        "WORKPILOT_CHALLENGER_MODEL=kimi-k3",
        "WORKPILOT_WORKSPACE=workspace",
        "WORKPILOT_TASK_DB=workspace/workpilot.sqlite",
        "WORKPILOT_RETENTION_DAYS=30",
        "WORKPILOT_MAX_ACTIVE_TASKS=3",
        "WORKPILOT_DOMAIN=http://localhost",
        "WORKPILOT_ADMIN_USER=admin",
        f"WORKPILOT_ADMIN_HASH={quote(password_hash)}",
        f"WORKPILOT_ADMIN_PASSWORD={quote(password)}",
        "",
    ]
    return "\n".join(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attachment", type=Path, default=DEFAULT_ATTACHMENT)
    parser.add_argument("--output", type=Path, default=Path(".env"))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.output.exists() and not args.force:
        raise FileExistsError(f"refusing to overwrite {args.output}; pass --force")
    keys = extract_keys(args.attachment)
    password = secrets.token_urlsafe(18)
    password_hash = caddy_hash(password)
    args.output.write_text(render_env(keys, password, password_hash), encoding="utf-8", newline="\n")
    print(f"created {args.output} with four key aliases and local Basic Auth")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

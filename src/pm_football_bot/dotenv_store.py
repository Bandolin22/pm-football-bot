from __future__ import annotations

import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def mask_secret(value: str, keep: int = 4) -> str:
    text = (value or "").strip()
    if len(text) <= keep * 2:
        return "••••"
    return f"{text[:keep]}…{text[-keep:]}"


def upsert_dotenv(values: dict[str, str], path: Path | None = None) -> None:
    """Write keys into .env and os.environ. Does not log values."""
    target = path or ENV_PATH
    rows: list[str] = []
    if target.exists():
        rows = target.read_text(encoding="utf-8").splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for line in rows:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in values:
                out.append(f"{key}={values[key]}")
                seen.add(key)
                continue
        out.append(line)
    for key, value in values.items():
        if key in seen:
            continue
        if out and out[-1].strip():
            out.append("")
        out.append(f"{key}={value}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    for key, value in values.items():
        os.environ[key] = value


def delete_dotenv_keys(keys: list[str], path: Path | None = None) -> None:
    """Remove keys from .env and the process environment."""
    target = path or ENV_PATH
    wanted = {key.strip() for key in keys if key.strip()}
    if target.exists():
        out: list[str] = []
        for line in target.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in wanted:
                    continue
            out.append(line)
        target.write_text(("\n".join(out).rstrip() + "\n") if out else "", encoding="utf-8")
    for key in wanted:
        os.environ.pop(key, None)

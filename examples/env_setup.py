from __future__ import annotations

import os
from pathlib import Path

from openai import AsyncOpenAI

from agents import (
    set_default_openai_api,
    set_default_openai_client,
    set_default_openai_key,
    set_tracing_disabled,
)


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def load_dotenv_file(path: str = ".env", *, override: bool = False) -> None:
    dotenv_path = Path(path)
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if "#" in value and not value.startswith(("'", '"')):
            value = value.split("#", 1)[0].strip()
        value = _strip_quotes(value)

        if override or key not in os.environ:
            os.environ[key] = value


def setup_examples_llm(path: str = ".env") -> None:
    load_dotenv_file(path)

    api_key = os.getenv("OPENAI_API_KEY")
    base_url = os.getenv("OPENAI_BASE_URL")

    if base_url and not api_key:
        raise ValueError("OPENAI_BASE_URL is set but OPENAI_API_KEY is missing.")

    if api_key and base_url:
        client = AsyncOpenAI(base_url=base_url, api_key=api_key)
        set_default_openai_client(client=client, use_for_tracing=False)
    elif api_key:
        set_default_openai_key(api_key, use_for_tracing=False)

    set_default_openai_api("chat_completions")

    if _is_truthy(os.getenv("OPENAI_AGENTS_DISABLE_TRACING")):
        set_tracing_disabled(True)


# Importing this module applies local .env + provider compatibility defaults for examples.
setup_examples_llm()

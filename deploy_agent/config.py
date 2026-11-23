"""Configuration for the deployment Deep Agent (no secrets in source)."""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_BASE_URL = "http://127.0.0.1:38285/v1"
DEFAULT_MODEL = "MiMo-V2.6-Distill-Qwen-9B-Q8_0"
DEFAULT_AGENT_MODEL = "MiMo-V2.6-Distill-Qwen-9B-Q8_0"
KEY_GLOB = ".unsloth/studio/auth/llama_api_key_*"


def api_key() -> str:
    """Resolve the local llama-server API key from env or the auth file."""
    explicit = os.environ.get("ECOMOPS_DEEPAGENTS_API_KEY", "").strip()
    if explicit:
        return explicit
    configured = os.environ.get("ECOMOPS_DEEPAGENTS_KEY_FILE", "").strip()
    candidates = (
        [Path(configured)]
        if configured
        else list(Path.home().glob(KEY_GLOB))
    )
    for path in candidates:
        try:
            value = path.read_text().strip()
        except OSError:
            continue
        if value:
            return value
    raise RuntimeError(
        "no local model API key — set ECOMOPS_DEEPAGENTS_API_KEY or "
        "ECOMOPS_DEEPAGENTS_KEY_FILE"
    )


def model():
    """Build the OpenAI-compatible chat model used by the Deep Agent."""
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=os.environ.get("ECOMOPS_DEEPAGENTS_MODEL", DEFAULT_AGENT_MODEL),
        base_url=os.environ.get("ECOMOPS_DEEPAGENTS_BASE_URL", DEFAULT_BASE_URL),
        api_key=api_key(),
        temperature=0,
        timeout=180,
        max_retries=1,
    )


__all__ = ["REPO_ROOT", "DEFAULT_BASE_URL", "DEFAULT_MODEL", "api_key", "model"]

"""Local chat-model connector for the MSM assistant.

Two ways to run a chat model, chosen by ``mode``:

* ``endpoint`` — talk to an already-running OpenAI-compatible server
  (Lemonade, llama.cpp, Ollama, vLLM, ...).
* ``gguf`` — point at a ``.gguf`` file; the connector starts ``llama-server``
  for it on demand and stops it on sign-out/shutdown.

Both degrade to ``None`` when the model is absent, so :class:`OpsChat` keeps
answering from its deterministic rules. No model is downloaded or started
until a GGUF path is configured.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "http://127.0.0.1:13305/api/v1"
DEFAULT_LOCAL_MODEL = "Llama-3.2-1B-Instruct-GGUF"
DEFAULT_LLAMA_PORT = 38285
DEFAULT_ALIAS = "msm-chat"

MODES = ("off", "endpoint", "gguf")
_MODEL_CACHE: dict[str, str] = {}


class ChatModelError(RuntimeError):
    """The connector could not start or reach the configured model."""


@dataclass(frozen=True)
class ChatModelConfig:
    """Connection settings. Secrets are hidden in the repr."""

    mode: str = "endpoint"
    base_url: str = DEFAULT_BASE_URL
    model: str = ""
    api_key: str = field(default="lemonade", repr=False)
    gguf_path: str = ""
    llama_server: str = "llama-server"
    port: int = DEFAULT_LLAMA_PORT
    context: int = 8192
    gpu_layers: int = -1
    timeout: float = 120.0
    max_tokens: int = 220

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ChatModelError(f"unknown chat model mode: {self.mode!r}")
        if not 1 <= self.port <= 65535:
            raise ChatModelError("port must be between 1 and 65535")

    @property
    def endpoint(self) -> str:
        if self.mode == "gguf":
            return f"http://127.0.0.1:{self.port}/v1"
        return self.base_url.rstrip("/")

    @property
    def model_name(self) -> str:
        if self.mode == "gguf":
            return self.model or DEFAULT_ALIAS
        return self.model

    @classmethod
    def from_environment(cls) -> ChatModelConfig:
        def _int(name: str, default: int) -> int:
            try:
                return int(os.environ.get(name, "").strip())
            except (TypeError, ValueError):
                return default

        def _float(name: str, default: float) -> float:
            try:
                return float(os.environ.get(name, "").strip())
            except (TypeError, ValueError):
                return default

        return cls(
            mode=os.environ.get("ECOMOPS_CHAT_MODE", "endpoint").strip().lower(),
            base_url=(
                os.environ.get("ECOMOPS_CHAT_BASE_URL")
                or os.environ.get("LEMONADE_BASE_URL")
                or DEFAULT_BASE_URL
            ),
            model=os.environ.get("ECOMOPS_CHAT_MODEL", "").strip(),
            api_key=os.environ.get("ECOMOPS_CHAT_API_KEY", "lemonade"),
            gguf_path=os.environ.get("ECOMOPS_CHAT_GGUF", "").strip(),
            llama_server=os.environ.get("ECOMOPS_CHAT_LLAMA_SERVER", "llama-server").strip(),
            port=_int("ECOMOPS_CHAT_PORT", DEFAULT_LLAMA_PORT),
            context=_int("ECOMOPS_CHAT_CTX", 8192),
            gpu_layers=_int("ECOMOPS_CHAT_NGL", -1),
            timeout=_float("ECOMOPS_CHAT_TIMEOUT", 120.0),
            max_tokens=_int("ECOMOPS_CHAT_MAX_TOKENS", 220),
        )


def _reachable(endpoint: str, *, timeout: float = 1.5) -> bool:
    """Whether an OpenAI-compatible server answers at ``endpoint``."""
    try:
        with urllib.request.urlopen(endpoint + "/models", timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def discover_model(endpoint: str) -> str | None:
    """First advertised model id, or ``None`` when nothing is loaded."""
    cached = _MODEL_CACHE.get(endpoint)
    if cached:
        return cached
    try:
        with urllib.request.urlopen(endpoint + "/models", timeout=2) as response:
            payload = json.load(response)
        models = payload.get("data") or []
        model = str(models[0].get("id") or "") or None if models else None
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        model = None
    if model:
        _MODEL_CACHE[endpoint] = model
    return model


class ChatConnector:
    """Resolve, start and talk to the configured local chat model.

    ``client`` in :meth:`invoke` is injectable for offline tests; production
    uses LangChain's OpenAI-compatible chat model.
    """

    def __init__(self, config: ChatModelConfig | None = None) -> None:
        self.config = config or ChatModelConfig.from_environment()
        self._process: subprocess.Popen | None = None

    # ------------------------------------------------------------- lifecycle

    def gguf_ready(self) -> bool:
        return bool(self.config.gguf_path) and Path(self.config.gguf_path).is_file()

    def server_binary(self) -> str | None:
        return shutil.which(self.config.llama_server)

    def ensure_ready(self) -> bool:
        """Make the configured endpoint reachable; never raises for a missing model."""
        if self.config.mode == "off":
            return False
        if self.config.mode == "endpoint":
            return _reachable(self.config.endpoint)
        if not self.gguf_ready() or not self.server_binary():
            return False
        if _reachable(self.config.endpoint):
            return True
        return self._start_llama_server()

    def _start_llama_server(self) -> bool:
        assert self._process is None
        binary = self.server_binary()
        if binary is None:  # pragma: no cover - guarded by ensure_ready
            return False
        args = [
            binary,
            "-m", str(self.config.gguf_path),
            "--port", str(self.config.port),
            "--alias", self.config.model_name,
            "-c", str(self.config.context),
            "-ngl", str(self.config.gpu_layers),
        ]
        try:
            self._process = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            self._process = None
            return False
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                self._process = None
                return False
            if _reachable(self.config.endpoint):
                return True
            time.sleep(0.3)
        self.close()
        return False

    def close(self) -> None:
        """Stop a llama-server this connector started (never a foreign process)."""
        if self._process is None:
            return
        with contextlib.suppress(Exception):
            self._process.terminate()
            self._process.wait(timeout=5)
        self._process = None

    # --------------------------------------------------------------- invoke

    def status(self) -> str:
        if self.config.mode == "off":
            return "disabled — deterministic rules only"
        if self.config.mode == "gguf":
            if not self.gguf_ready():
                return "GGUF path not set or file missing"
            if not self.server_binary():
                return f"{self.config.llama_server!r} not on PATH"
            return "ready" if _reachable(self.config.endpoint) else "stopped (starts on first question)"
        return "ready" if _reachable(self.config.endpoint) else "endpoint unreachable"

    def invoke(
        self,
        system_prompt: str,
        question: str,
        snapshot: str,
        *,
        client: Any = None,
    ) -> str | None:
        """Answer via the configured model, or ``None`` to use rule fallback."""
        if os.environ.get("ECOMOPS_CHAT_DISABLE_LLM") == "1":
            return None
        if not self.ensure_ready():
            return None
        model = self.config.model_name or discover_model(self.config.endpoint)
        if self.config.mode == "endpoint" and not model:
            model = self.config.model or DEFAULT_LOCAL_MODEL
        if not model:
            return None
        try:
            if client is None:
                from langchain_core.messages import HumanMessage, SystemMessage
                from langchain_openai import ChatOpenAI

                client = ChatOpenAI(
                    model=model,
                    base_url=self.config.endpoint,
                    api_key=self.config.api_key,
                    temperature=0,
                    timeout=self.config.timeout,
                    max_retries=0,
                    max_tokens=self.config.max_tokens,
                )
                response = client.invoke([
                    SystemMessage(content=f"{system_prompt}\n\nSnapshot: {snapshot}"),
                    HumanMessage(content=question),
                ])
                return str(getattr(response, "content", "") or "").strip() or None

            # Injected client: a callable or an object with .invoke(messages).
            messages = [
                {"role": "system", "content": f"{system_prompt}\n\nSnapshot: {snapshot}"},
                {"role": "user", "content": question},
            ]
            response = client(messages) if callable(client) else client.invoke(messages)
            content = getattr(response, "content", response)
            return str(content).strip() or None
        except Exception:
            return None


# Module-level connector: one managed llama-server per process.
_CONNECTOR: ChatConnector | None = None


def get_connector(config: ChatModelConfig | None = None) -> ChatConnector:
    """Return the shared connector, rebuilding it when settings change."""
    global _CONNECTOR
    chosen = config or ChatModelConfig.from_environment()
    if _CONNECTOR is None or _CONNECTOR.config != chosen:
        if _CONNECTOR is not None:
            _CONNECTOR.close()
        _CONNECTOR = ChatConnector(chosen)
    return _CONNECTOR


__all__ = [
    "DEFAULT_ALIAS",
    "DEFAULT_BASE_URL",
    "DEFAULT_LLAMA_PORT",
    "DEFAULT_LOCAL_MODEL",
    "MODES",
    "ChatConnector",
    "ChatModelConfig",
    "ChatModelError",
    "discover_model",
    "get_connector",
]

"""LLM access layer: local MLX (mlx-lm) with a deterministic fallback.

Agents never talk to a provider directly. They call ``llm.complete(task=...)``
with a task name, natural-language instructions and a JSON payload; the
provider returns a JSON dict which the agent validates with a pydantic model.

Two backends implement the same contract:

* ``MlxLLM``         — runs a local MLX model (``mlx-community/Qwen3-4B-4bit``
                      by default) via ``mlx-lm`` on Apple Silicon. No network,
                      no API key; the model stays resident in memory between
                      calls so the first call pays the load cost and the rest
                      are fast. Generation runs in a worker thread so the
                      event loop is never blocked.
* ``RuleBasedLLM``  — deterministic handlers keyed by task name. Used in tests,
                      in CI, and automatically whenever MLX is unavailable
                      (not installed, or the model cannot load), so the whole
                      system degrades gracefully instead of dying.

``FallbackLLM`` wraps the two: try the real model, fall back on any failure,
and report which backend actually answered (recorded on events — you can always
see whether a decision came from the model or the rules).

Model choice is a trade-off documented in AGENTICAI_PLAN.md §5: the 4B runs at
~15 tok/s on an M2 and is the default; the 8B is ~2x slower but reasons better
on the hard parts. Override with the ``model=`` argument or the
``ECOMOPS_MLX_MODEL`` env var.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Protocol

log = logging.getLogger("ecomops.llm")

DEFAULT_MLX_MODEL = "mlx-community/Qwen3-4B-4bit"


class LLMUnavailable(RuntimeError):
    """The model could not be reached or produced unparseable output."""


@dataclass(frozen=True)
class LLMResult:
    data: dict[str, Any]
    backend: str                    # "mlx:<model>" | "rule-based" | "fallback:<...>"
    model: str | None = None


class LLM(Protocol):
    async def complete(
        self,
        *,
        task: str,
        instructions: str,
        payload: dict[str, Any],
        schema: dict[str, Any] | None = None,
    ) -> LLMResult: ...


_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


def parse_json_object(text: str) -> dict[str, Any]:
    """Extract a JSON object from model output, tolerating code fences and prose."""
    m = _JSON_FENCE.search(text)
    if m:
        text = m.group(1)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise


_SYSTEM = (
    "You are a logistics operations planner. "
    "Answer with a single JSON object and nothing else. Never invent ids."
)


@dataclass
class MlxLLM:
    """Local MLX backend (mlx-lm). No network, no API key.

    The model and tokenizer are loaded once and kept resident — repeated
    ``complete`` calls reuse them, so only the first call pays the load cost.
    Generation is CPU/GPU-bound and synchronous, so it runs in a worker thread
    to keep the event bus responsive.
    """

    model: str = os.environ.get("ECOMOPS_MLX_MODEL", DEFAULT_MLX_MODEL)
    max_tokens: int = 400
    temp: float = 0.2
    _loaded: dict[str, tuple[Any, Any]] = field(default_factory=dict, repr=False)

    @property
    def backend_name(self) -> str:
        return f"mlx:{self.model}"

    # ------------------------------------------------------------- loading

    def _load(self) -> tuple[Any, Any]:
        """Load (and cache) the model+tokenizer, or raise LLMUnavailable."""
        if self.model in self._loaded:
            return self._loaded[self.model]
        try:
            from mlx_lm import load
        except ImportError as exc:
            raise LLMUnavailable(
                "mlx-lm is not installed — pip install 'ecomops[mlx]'"
            ) from exc
        try:
            model, tokenizer = load(self.model)
        except Exception as exc:  # any load failure must degrade, not crash
            raise LLMUnavailable(f"cannot load MLX model {self.model!r}: {exc}") from exc
        self._loaded[self.model] = (model, tokenizer)
        log.info("loaded MLX model %s", self.model)
        return model, tokenizer

    # ---------------------------------------------------------- generation

    def _prompt(self, instructions: str, payload: dict[str, Any], schema: dict | None) -> str:
        user = f"{instructions}\n\nInput:\n{json.dumps(payload, indent=2)}"
        if schema is not None:
            user += f"\n\nRespond with ONE JSON object matching this JSON Schema:\n{json.dumps(schema)}"
        return user

    def _chat_prompt(self, tokenizer: Any, user: str) -> str:
        """Render the chat template. Disables Qwen3-family "thinking".

        The ``enable_thinking`` switch is a *chat-template* kwarg, not a
        ``generate`` kwarg: when the template supports it (Qwen3), it emits a
        non-thinking system prefix so the model answers with bare JSON instead
        of a chain-of-thought preamble. Templates that don't know the kwarg
        ignore it (Jinja treats unknown names as undefined), so this is safe to
        pass unconditionally; we still retry without it in case a non-Jinja
        tokenizer rejects the argument outright.
        """
        if hasattr(tokenizer, "apply_chat_template"):
            messages = [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ]
            try:
                return tokenizer.apply_chat_template(
                    messages,
                    tokenize=False,
                    add_generation_prompt=True,
                    enable_thinking=False,
                )
            except TypeError:
                try:
                    return tokenizer.apply_chat_template(
                        messages, tokenize=False, add_generation_prompt=True
                    )
                except Exception:  # fall through to plain formatting
                    pass
            except Exception:  # fall through to plain formatting
                pass
        return f"System: {_SYSTEM}\nUser: {user}\nAssistant:"

    def _generate_sync(self, prompt: str) -> str:
        """Blocking generation — runs in a worker thread via asyncio.to_thread."""
        from mlx_lm import generate
        from mlx_lm.sample_utils import make_sampler

        model, tokenizer = self._load()
        sampler = make_sampler(temp=self.temp)
        return generate(
            model,
            tokenizer,
            prompt=prompt,
            max_tokens=self.max_tokens,
            sampler=sampler,
            verbose=False,
        )

    async def complete(
        self,
        *,
        task: str,
        instructions: str,
        payload: dict[str, Any],
        schema: dict[str, Any] | None = None,
    ) -> LLMResult:
        _model, tokenizer = self._load()          # surfaces load errors as LLMUnavailable
        prompt = self._chat_prompt(tokenizer, self._prompt(instructions, payload, schema))
        try:
            content = await asyncio.to_thread(self._generate_sync, prompt)
        except LLMUnavailable:
            raise
        except Exception as exc:  # degrade to fallback on any runtime error
            raise LLMUnavailable(f"mlx generation failed: {exc}") from exc
        try:
            data = parse_json_object(content)
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMUnavailable(f"unparseable model output: {exc}") from exc
        return LLMResult(data=data, backend=self.backend_name, model=self.model)


Rule = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class RuleBasedLLM:
    """Deterministic stand-in: one hand-written rule per task.

    Not an 'LLM' in any real sense — its value is that it keeps the system, the
    demo and the test suite runnable with no model, no GPU and no network.
    """

    rules: dict[str, Rule] = field(default_factory=dict)

    backend_name = "rule-based"

    def register(self, task: str, rule: Rule) -> None:
        self.rules[task] = rule

    async def complete(
        self,
        *,
        task: str,
        instructions: str,
        payload: dict[str, Any],
        schema: dict[str, Any] | None = None,
    ) -> LLMResult:
        rule = self.rules.get(task)
        if rule is None:
            raise LLMUnavailable(f"no rule registered for task {task!r}")
        return LLMResult(data=rule(payload), backend=self.backend_name)


@dataclass
class FallbackLLM:
    """Try ``primary``; on any failure use ``secondary``. Never raises LLMUnavailable."""

    primary: LLM
    secondary: LLM

    async def complete(
        self,
        *,
        task: str,
        instructions: str,
        payload: dict[str, Any],
        schema: dict[str, Any] | None = None,
    ) -> LLMResult:
        try:
            return await self.primary.complete(
                task=task, instructions=instructions, payload=payload, schema=schema
            )
        except LLMUnavailable as exc:
            log.warning("primary backend failed (%s); using fallback for task %r", exc, task)
            result = await self.secondary.complete(
                task=task, instructions=instructions, payload=payload, schema=schema
            )
            return LLMResult(data=result.data, backend=f"fallback:{self.secondary.backend_name}", model=None)


# --------------------------------------------------------------------------- #
# Embeddings — sentence-transformers when installed, token overlap otherwise
# --------------------------------------------------------------------------- #

_TOKEN = re.compile(r"[a-z0-9]+")


@lru_cache(maxsize=8192)
def _token_set(text: str) -> frozenset[str]:
    """Cached token set — the grocery pack has 1,700+ long product names."""
    return frozenset(_TOKEN.findall(text.lower()))


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


class Embedder(Protocol):
    name: str

    def similarity(self, a: str, b: str) -> float: ...


@dataclass
class MiniLMEmbedder:
    """all-MiniLM-L6-v2 via sentence-transformers (optional dependency).

    ``similarities`` batches and caches title vectors: the grocery pack has
    1,500+ long names, and encoding them one pair at a time would dominate a
    request. ``Inventory`` uses it when present and falls back to ``similarity``
    for simpler embedders.
    """

    model_name: str = "all-MiniLM-L6-v2"
    name: str = "all-MiniLM-L6-v2"

    def __post_init__(self) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self.model_name)
        self._cache: dict[str, list[float]] = {}

    def _encode(self, texts: list[str]) -> list[list[float]]:
        missing = [text for text in dict.fromkeys(texts) if text not in self._cache]
        if missing:
            vectors = self._model.encode(missing)
            for text, vector in zip(missing, vectors, strict=True):
                self._cache[text] = [float(x) for x in vector]
        return [self._cache[text] for text in texts]

    def similarity(self, a: str, b: str) -> float:
        va, vb = self._encode([a, b])
        return float(cosine(va, vb))

    def similarities(self, query: str, texts: list[str]) -> list[float]:
        query_vec, *vectors = self._encode([query, *texts])
        return [float(cosine(query_vec, vector)) for vector in vectors]


@dataclass
class TokenOverlapEmbedder:
    """Offline lexical embedder: Jaccard plus full-token coverage.

    Pure Jaccard punishes long catalogue titles: a short query like
    ``"paper towels"`` only scores ~0.2 against
    ``"Bounty Advanced Paper Towels, 2-Ply, …"``.  So when *every* token of the
    shorter text appears in the longer one, that is treated as a strong match,
    scaled by how much extra text the longer side carries.  A single shared
    token never qualifies, so unrelated queries still fall below the threshold.
    """

    name: str = "token-overlap"

    def similarity(self, a: str, b: str) -> float:
        ta, tb = _token_set(a), _token_set(b)
        if not ta or not tb:
            return 0.0
        overlap = len(ta & tb)
        jaccard = overlap / len(ta | tb)
        short, long = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        if len(short) <= len(long) and overlap == len(short):
            return max(jaccard, 0.6 + 0.4 * (len(short) / len(long)))
        return jaccard


def default_embedder() -> Embedder:
    """Prefer MiniLM when sentence-transformers is installed; else token overlap."""
    try:
        return MiniLMEmbedder()
    except Exception:
        log.info("sentence-transformers unavailable; using token-overlap embedder")
        return TokenOverlapEmbedder()

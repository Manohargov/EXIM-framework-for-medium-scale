"""Chat connector: config, GGUF lifecycle and offline model invocation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ecomops import chatmodel
from ecomops.chatmodel import (
    ChatConnector,
    ChatModelConfig,
    ChatModelError,
)


class _Response:
    def __init__(self, content: str) -> None:
        self.content = content


def test_config_from_environment(monkeypatch):
    monkeypatch.setenv("ECOMOPS_CHAT_MODE", "gguf")
    monkeypatch.setenv("ECOMOPS_CHAT_GGUF", "/models/chat.gguf")
    monkeypatch.setenv("ECOMOPS_CHAT_PORT", "45000")
    monkeypatch.setenv("ECOMOPS_CHAT_API_KEY", "secret-key")
    config = ChatModelConfig.from_environment()

    assert config.mode == "gguf"
    assert config.gguf_path == "/models/chat.gguf"
    assert config.port == 45000
    assert config.endpoint == "http://127.0.0.1:45000/v1"
    assert config.model_name == "msm-chat"
    assert "secret-key" not in repr(config)


def test_invalid_configs_are_rejected():
    with pytest.raises(ChatModelError):
        ChatModelConfig(mode="bogus")
    with pytest.raises(ChatModelError):
        ChatModelConfig(port=0)


def test_endpoint_mode_uses_discovered_or_default_model(monkeypatch):
    config = ChatModelConfig(mode="endpoint", base_url="http://127.0.0.1/v1")
    connector = ChatConnector(config)
    monkeypatch.setattr(chatmodel, "_reachable", lambda *_a, **_k: True)
    monkeypatch.setattr(chatmodel, "discover_model", lambda _url: None)

    seen: dict[str, str] = {}

    def client(messages):
        seen["system"] = messages[0]["content"]
        return _Response("Three shipments routed.")

    text = connector.invoke("Be brief", "How many shipments?", '{"shipments": 3}', client=client)
    assert text == "Three shipments routed."
    assert "Snapshot:" in seen["system"]
    assert config.model_name == ""  # resolved at call time, not in config


def test_injected_callable_and_object_client(monkeypatch):
    config = ChatModelConfig(mode="endpoint", model="test-model")
    connector = ChatConnector(config)
    monkeypatch.setattr(chatmodel, "_reachable", lambda *_a, **_k: True)
    assert connector.invoke("sys", "q", "{}", client=lambda _m: _Response("ok")) == "ok"

    class Client:
        def invoke(self, messages):
            return "from-object"

    assert connector.invoke("sys", "q", "{}", client=Client()) == "from-object"


def test_no_model_means_rule_fallback(monkeypatch):
    connector = ChatConnector(ChatModelConfig(mode="off"))
    assert connector.invoke("sys", "q", "{}", client=lambda _m: "should not run") is None
    assert "disabled" in connector.status()


def test_gguf_mode_requires_file_and_binary(tmp_path, monkeypatch):
    missing = ChatConnector(ChatModelConfig(mode="gguf", gguf_path=str(tmp_path / "none.gguf")))
    assert not missing.ensure_ready()
    assert "not set or file missing" in missing.status()

    gguf = tmp_path / "chat.gguf"
    gguf.write_bytes(b"not a real model")
    monkeypatch.setattr(ChatConnector, "server_binary", lambda self: None)
    present = ChatConnector(ChatModelConfig(mode="gguf", gguf_path=str(gguf)))
    assert not present.ensure_ready()
    assert "not on PATH" in present.status()


def test_gguf_mode_starts_and_stops_its_own_server(tmp_path, monkeypatch):
    gguf = tmp_path / "chat.gguf"
    gguf.write_bytes(b"x")
    calls: list[list[str]] = []

    class FakeProcess:
        def poll(self):
            return None

        def terminate(self):
            calls.append(["terminate"])

        def wait(self, timeout=5):
            return 0

    monkeypatch.setattr(ChatConnector, "server_binary", lambda self: "/usr/bin/llama-server")
    monkeypatch.setattr(chatmodel.subprocess, "Popen", lambda args, **_k: (calls.append(args), FakeProcess())[1])
    probes = {"count": 0}

    def reachable(*_a, **_k):
        probes["count"] += 1
        return probes["count"] > 1  # first check (before start) is down

    monkeypatch.setattr(chatmodel, "_reachable", reachable)
    monkeypatch.setattr(chatmodel.time, "sleep", lambda _s: None)

    connector = ChatConnector(
        ChatModelConfig(mode="gguf", gguf_path=str(gguf), port=45001, context=4096)
    )
    assert connector.ensure_ready()
    assert calls[0][:4] == ["/usr/bin/llama-server", "-m", str(gguf), "--port"]
    assert "45001" in calls[0]
    connector.close()
    assert ["terminate"] in calls


def test_shared_connector_rebuilds_on_config_change():
    first = chatmodel.get_connector(ChatModelConfig(mode="off"))
    second = chatmodel.get_connector(ChatModelConfig(mode="off"))
    assert first is second
    third = chatmodel.get_connector(ChatModelConfig(mode="endpoint"))
    assert third is not first
    chatmodel.get_connector(ChatModelConfig(mode="off"))


def test_grounding_still_applies_when_model_invents_numbers(monkeypatch):
    from ecomops import chat
    from ecomops.auth import Role, User
    from ecomops.system import build_system

    monkeypatch.delenv("ECOMOPS_CHAT_DISABLE_LLM", raising=False)
    monkeypatch.setattr(
        chat,
        "get_connector",
        lambda: SimpleNamespace(invoke=lambda *_a, **_k: "We have 999 shipments."),
    )
    assistant = chat.OpsChat(build_system(), User("u@msm.local", "U", Role.STAFF))
    assert assistant._langchain_answer("how many shipments?") is None

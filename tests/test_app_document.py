"""Assistant document panel, exercised through Streamlit's AppTest.

The pipeline tests cover extraction, resolution and rendering; this test covers
the sidebar wiring: an attached job appears, answers are applied, and the
generated PDF can be downloaded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest

from ecomops.auth import Role, demo_user
from ecomops.documents import discard, open_document
from ecomops.system import build_system

APP = Path(__file__).parents[1] / "src" / "ecomops" / "app.py"
RETURN_SLIP = Path(__file__).parent / "fixtures" / "documents" / "return_slip.pdf"


def _button(at: AppTest, label: str):
    return next(button for button in at.button if button.label == label)


@pytest.fixture()
def session():
    system = build_system()
    job = open_document(system, demo_user(Role.MD), RETURN_SLIP)
    yield job
    discard(job)


def test_assistant_panel_reviews_answers_and_generates(session, monkeypatch):
    monkeypatch.setenv("ECOMOPS_AUTH_AUTOLOGIN", "md")
    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    at.toggle[0].set_value(True).run()

    at.session_state["doc_session"] = session
    at.run()

    assert not at.exception
    assert any(field.key == "doc_answer_text" for field in at.text_input)
    assert any(choice.label == "Format" for choice in at.radio)
    # The document is open, so the download only appears after generating.
    assert not at.get("download_button")

    at.text_input(key="doc_answer_text").set_value("sku=SKU-1001\nshipment_id=")
    _button(at, "Apply answers").click()
    at.run()
    assert not at.exception
    assert session.ready

    _button(at, "Generate document").click()
    at.run()
    assert not at.exception

    output = Path(at.session_state["doc_output"])
    assert output.exists()
    assert output.read_bytes().startswith(b"%PDF")
    assert [button.label for button in at.get("download_button")] == ["Download filled PDF"]

    _button(at, "Discard document").click()
    at.run()
    assert not at.exception
    assert "doc_session" not in at.session_state


def test_chat_accepts_key_value_answers_for_the_open_document(session, monkeypatch):
    monkeypatch.setenv("ECOMOPS_AUTH_AUTOLOGIN", "md")
    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    at.toggle[0].set_value(True).run()
    at.session_state["doc_session"] = session
    at.run()

    at.chat_input[0].set_value("sku=SKU-1001\nshipment_id=")
    at.run()

    assert not at.exception
    assert session.ready
    assert at.session_state["chat_history"][-2]["role"] == "user"
    assert "answered from MSM" in at.session_state["chat_history"][-1]["content"]


def test_chat_question_is_never_mistaken_for_an_answer(session, monkeypatch):
    monkeypatch.setenv("ECOMOPS_AUTH_AUTOLOGIN", "md")
    at = AppTest.from_file(str(APP), default_timeout=180)
    at.run()
    at.toggle[0].set_value(True).run()
    at.session_state["doc_session"] = session
    at.run()

    at.chat_input[0].set_value("what is pending?")
    at.run()

    assert not at.exception
    assert not session.ready
    reply = at.session_state["chat_history"][-1]
    assert reply["role"] == "assistant"
    assert "answered from MSM" not in reply["content"]

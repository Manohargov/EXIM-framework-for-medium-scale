"""Sidebar assistant: deterministic intents, role gating, summary fallback."""

from pathlib import Path

import pytest

from ecomops.auth import Permission, Role, User
from ecomops.chat import OpsChat
from ecomops.system import build_system


def _user(role: Role) -> User:
    return User(email=f"{role.value}@msm.local", name="Test User", role=role)


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    # The optional LangChain backend is not installed in CI; keep answers deterministic.
    monkeypatch.setenv("ECOMOPS_CHAT_DISABLE_LLM", "1")


@pytest.fixture
def system():
    return build_system()


def test_greeting_uses_name_and_role(system):
    reply = OpsChat(system, _user(Role.STAFF)).answer("hello")
    assert "Test" in reply and "Staff" in reply


def test_permission_question_lists_allowed_scopes(system):
    reply = OpsChat(system, _user(Role.SUPERVISOR)).answer("what are my permissions?")
    assert "view_operations" in reply and "view_audit" in reply
    assert "view_finance" not in reply


def test_staff_cannot_see_approvals_finance_or_audit(system):
    chat = OpsChat(system, _user(Role.STAFF))
    assert "cannot view the approval queue" in chat.answer("what is pending?")
    assert "restricted to the accountant and the MD" in chat.answer("what is our revenue?")
    assert "restricted to supervisors and the MD" in chat.answer("show the event trace")


def test_accountant_sees_finance_but_not_operations_queue(system):
    chat = OpsChat(system, _user(Role.ACCOUNTANT))
    reply = chat.answer("refund total?")
    assert reply.startswith("Finance · ") and "gross $" in reply

    # The accountant can see money approvals, but the operations queue is out of scope.
    assert "pending" in chat.answer("what is pending?").lower()


def test_supervisor_sees_audit_and_empty_queue(system):
    chat = OpsChat(system, _user(Role.SUPERVISOR))
    assert chat.answer("what is pending?") == "Nothing pending in your approval scope."
    assert "events on the bus" in chat.answer("event trace")


def test_stock_question_matches_the_catalogue(system):
    reply = OpsChat(system, _user(Role.STAFF)).answer("stock of wireless bluetooth earbuds")
    assert "SKU-1001" in reply and "units on hand" in reply


def test_unknown_product_is_reported_not_guessed(system):
    reply = OpsChat(system, _user(Role.STAFF)).answer("stock of quantum flux capacitor")
    assert "could not match" in reply


def test_returns_question_reports_counts(system):
    reply = OpsChat(system, _user(Role.STAFF)).answer("how many returns?")
    assert "0 return(s) assessed" in reply


async def test_shipment_question_after_dispatch(system):
    await system.receive_customer_request("CUST-CHAT", "1 wireless bluetooth earbuds to Berlin")
    for event in list(system.pending_approvals):
        await system.decide(event.proposal_id, granted=True, decided_by="tester")

    reply = OpsChat(system, _user(Role.STAFF)).answer("where is the shipment?")
    assert "1 shipment(s) routed" in reply and "Berlin" in reply


def test_finance_is_absent_from_staff_snapshot(system):
    staff_snapshot = OpsChat(system, _user(Role.STAFF)).snapshot()
    assert "finance" not in staff_snapshot
    assert staff_snapshot["user"]["role"] == "staff"

    md_snapshot = OpsChat(system, _user(Role.MD)).snapshot()
    assert "finance" in md_snapshot
    assert set(md_snapshot["permissions"]) == {permission.value for permission in Permission}


def test_unmatched_question_falls_back_to_summary(system):
    reply = OpsChat(system, _user(Role.MD)).answer("tell me a poem about warehouses")
    assert reply.startswith("Current state:") and "events" in reply
    assert "gross $" in reply  # the MD may see finance in the fallback


def test_blank_question_gets_a_hint(system):
    assert "Ask me about" in OpsChat(system, _user(Role.STAFF)).answer("   ")


def test_numbers_must_be_grounded_in_the_snapshot():
    from ecomops.chat import _numbers_grounded

    assert _numbers_grounded("3 events and $1,060 gross", '{"events": 3, "gross": 1060.0}')
    assert not _numbers_grounded("product #1234", '{"events": 3}')
    assert _numbers_grounded("no figures at all", '{"events": 0}')


def test_connector_defaults_match_the_documented_local_model():
    from ecomops.chatmodel import DEFAULT_LOCAL_MODEL, ChatModelConfig

    config = ChatModelConfig()
    assert config.mode == "endpoint"
    assert config.model_name == ""  # discovered from the endpoint at call time
    assert DEFAULT_LOCAL_MODEL == "Llama-3.2-1B-Instruct-GGUF"


async def test_customer_request_question_reads_the_event_store(system):
    chat = OpsChat(system, _user(Role.STAFF))
    assert "No customer requests" in chat.answer("how many customer requests came?")

    await system.receive_customer_request("CUST-42", "2 jars of peanut butter to Berlin")

    reply = chat.answer("how many customer request came?")
    assert "1 customer request(s) from 1 customer(s)" in reply
    assert "CUST-42" in reply


# --------------------------------------------------------------------------- #
# Document awareness
# --------------------------------------------------------------------------- #

RETURN_SLIP = Path(__file__).parent / "fixtures" / "documents" / "return_slip.pdf"


def test_chat_without_a_document_explains_attachments(system):
    reply = OpsChat(system, _user(Role.MD)).answer("can you read a document?")
    assert "Attach a PDF" in reply


def test_chat_with_open_document_reports_gaps_and_generation(system):
    from ecomops.documents import discard, open_document

    session = open_document(system, _user(Role.MD), RETURN_SLIP)
    try:
        chat = OpsChat(system, _user(Role.MD), document=session)
        snapshot = chat.snapshot()
        assert snapshot["document"]["filename"] == "return_slip.pdf"
        assert snapshot["document"]["still_needed"] == ["SKU"]
        assert snapshot["document"]["optional"] == ["Shipment"]

        missing = chat.answer("what is missing?")
        assert "SKU-104 is not in the catalogue" in missing
        assert "sku=SKU-1001" in missing

        blocked = chat.answer("can you generate the pdf?")
        assert "remaining field(s)" in blocked and "SKU" in blocked

        # Ordinary operations questions still route to their own intents.
        pending = chat.answer("what is pending?")
        assert "pending" in pending.lower()
        assert "return_slip.pdf" not in pending
    finally:
        discard(session)


def test_chat_with_answered_document_offers_generation(system):
    from ecomops.documents import answer as answer_document, discard, open_document

    user = _user(Role.MD)
    session = open_document(system, user, RETURN_SLIP)
    try:
        answer_document(system, user, session, "sku=SKU-1001")
        chat = OpsChat(system, user, document=session)
        reply = chat.answer("what is still missing?")
        assert "Nothing blocking is missing" in reply
        assert "Shipment" in reply  # optional, stays visible

        assert "Generate document" in chat.answer("how do I download it?")
    finally:
        discard(session)

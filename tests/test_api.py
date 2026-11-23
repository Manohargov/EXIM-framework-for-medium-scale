"""HTTP control plane: authentication, RBAC, approvals, read models, metrics."""

import base64
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from ecomops.api import create_app
from ecomops.system import build_system

PASSWORD = "msm"


def _token(client: TestClient, email: str) -> str:
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _as(client: TestClient, email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(client, email)}"}


@pytest.fixture
def client():
    with TestClient(create_app(build_system())) as c:
        c.headers.update(_as(c, "md@msm.local"))
        yield c


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #


def test_login_rejects_bad_credentials_and_token(client):
    assert client.post(
        "/auth/login", json={"email": "md@msm.local", "password": "nope"}
    ).status_code == 401
    assert client.post(
        "/auth/login", json={"email": "nobody@msm.local", "password": PASSWORD}
    ).status_code == 401

    anonymous = TestClient(client.app)
    assert anonymous.get("/inventory").status_code == 401
    assert anonymous.get("/inventory", headers={"Authorization": "Bearer forged.token"}).status_code == 401


def test_auth_me_lists_permissions(client):
    body = client.get("/auth/me").json()
    assert body["user"]["role"] == "md"
    assert "manage" in body["permissions"]
    assert "approve_finance" in body["permissions"]

    staff = client.get("/auth/me", headers=_as(client, "staff@msm.local")).json()
    assert staff["user"]["role"] == "staff"
    assert staff["permissions"] == ["act_operations", "view_operations"]


def test_health_is_open_but_metrics_requires_auth():
    with TestClient(create_app(build_system())) as c:
        assert c.get("/healthz").status_code == 200
        assert c.get("/metrics").status_code == 401


# --------------------------------------------------------------------------- #
# Role-based access control
# --------------------------------------------------------------------------- #


def test_staff_can_act_but_not_approve_or_see_finance(client):
    staff = _as(client, "staff@msm.local")

    assert client.get("/inventory", headers=staff).status_code == 200
    assert client.get("/shipments", headers=staff).status_code == 200
    assert client.get("/catalogue/match", params={"query": "earbuds"}, headers=staff).status_code == 200
    assert client.post(
        "/requests", json={"customer_id": "CUST-S", "text": "1 desk lamp led"}, headers=staff
    ).status_code == 200

    assert client.get("/approvals", headers=staff).status_code == 403
    assert client.get("/finance", headers=staff).status_code == 403
    assert client.get("/events", headers=staff).status_code == 403

    proposal = client.get("/approvals").json()["approvals"][0]
    denied = client.post(
        f"/approvals/{proposal['proposal_id']}", json={"granted": True}, headers=staff
    )
    assert denied.status_code == 403
    assert "approve_operations" in denied.json()["detail"].lower()


def test_supervisor_has_operations_and_audit_but_no_finance(client):
    supervisor = _as(client, "supervisor@msm.local")

    assert client.get("/approvals", headers=supervisor).status_code == 200
    assert client.get("/events", headers=supervisor).status_code == 200
    assert client.get("/inventory", headers=supervisor).status_code == 200
    assert client.get("/finance", headers=supervisor).status_code == 403


def test_accountant_sees_finance_only(client):
    accountant = _as(client, "accountant@msm.local")

    finance = client.get("/finance", headers=accountant)
    assert finance.status_code == 200
    assert {"gross_revenue", "refund_total", "referrals"} <= set(finance.json())

    for path in ("/inventory", "/shipments", "/events", "/metrics"):
        assert client.get(path, headers=accountant).status_code == 403, path
    assert client.post(
        "/requests", json={"customer_id": "CUST-A", "text": "1 desk lamp led"}, headers=accountant
    ).status_code == 403


def test_supervisor_can_approve_operations(client):
    supervisor = _as(client, "supervisor@msm.local")
    seeded = client.post(
        "/requests",
        json={"customer_id": "CUST-SUP", "text": "2 wireless bluetooth earbuds to Berlin ASAP"},
        headers=supervisor,
    )
    assert seeded.status_code == 200
    proposal = next(
        a for a in seeded.json()["pending_approvals"] if a["action"] != "issue_refund"
    )
    granted = client.post(
        f"/approvals/{proposal['proposal_id']}",
        json={"granted": True, "decided_by": "supervisor@msm.local"},
        headers=supervisor,
    )
    assert granted.status_code == 200


def test_finance_approvals_are_reserved_for_finance_roles(client):
    # Seed a refund with the MD, then try to approve it as staff/supervisor/accountant.
    client.post(
        "/requests", json={"customer_id": "CUST-F", "text": "1 wireless bluetooth earbuds"}
    )
    for approval in client.get("/approvals").json()["approvals"]:
        client.post(f"/approvals/{approval['proposal_id']}", json={"granted": True})

    seeded = client.post(
        "/requests",
        json={"customer_id": "CUST-F", "text": "I want a refund for the wireless bluetooth earbuds, damaged"},
    ).json()
    refund = next(a for a in seeded["pending_approvals"] if a["action"] == "issue_refund")
    pid = refund["proposal_id"]

    for email, expected in (
        ("supervisor@msm.local", 403),
        ("staff@msm.local", 403),
        ("accountant@msm.local", 200),
    ):
        response = client.post(
            f"/approvals/{pid}", json={"granted": True}, headers=_as(client, email)
        )
        assert response.status_code == expected, (email, response.text)

    topics = [e["topic"] for e in client.get("/events", params={"limit": 500}).json()["events"]]
    assert "RefundIssued" in topics


# --------------------------------------------------------------------------- #
# System behaviour (as the MD)
# --------------------------------------------------------------------------- #


def test_health_and_metrics(client):
    health = client.get("/healthz")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "ecomops_events_total" in metrics.text
    assert "ecomops_pending_approvals" in metrics.text


def test_request_creates_shipment_and_approval(client):
    response = client.post(
        "/requests",
        json={"customer_id": "CUST-API", "text": "2 wireless bluetooth earbuds to Berlin ASAP"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["event_count"] > 0
    assert any(a["action"] == "apply_discount" for a in body["pending_approvals"])

    shipments = client.get("/shipments").json()["shipments"]
    assert shipments and next(iter(shipments.values()))["destination"] == "Berlin"

    events = client.get("/events", params={"topic": "DemandSignal"}).json()
    assert events["count"] == 1 and events["events"][0]["topic"] == "DemandSignal"


def test_approval_grant_and_unknown_proposal(client):
    client.post("/requests", json={"customer_id": "CUST-API2", "text": "1 desk lamp led"})
    approvals = client.get("/approvals").json()["approvals"]
    pid = approvals[0]["proposal_id"]

    granted = client.post(
        f"/approvals/{pid}",
        json={"granted": True, "decided_by": "tester", "comment": "ok"},
    )
    assert granted.status_code == 200
    assert granted.json()["pending_approvals"] == []

    missing = client.post("/approvals/nope", json={"granted": True})
    assert missing.status_code == 404


def test_return_refund_through_the_api(client):
    client.post("/requests", json={"customer_id": "CUST-R", "text": "1 wireless bluetooth earbuds"})
    for approval in client.get("/approvals").json()["approvals"]:
        client.post(f"/approvals/{approval['proposal_id']}", json={"granted": True})

    response = client.post(
        "/requests",
        json={"customer_id": "CUST-R", "text": "I want a refund for the wireless bluetooth earbuds, damaged"},
    )
    refunds = [a for a in response.json()["pending_approvals"] if a["action"] == "issue_refund"]
    assert refunds, "refund should be waiting for a human"

    client.post(f"/approvals/{refunds[0]['proposal_id']}", json={"granted": True, "decided_by": "ops"})
    topics = [e["topic"] for e in client.get("/events", params={"limit": 500}).json()["events"]]
    assert "RefundIssued" in topics


def test_finance_read_model(client):
    snapshot = client.get("/finance").json()
    assert snapshot["gross_revenue"] == 0
    assert snapshot["referrals"] and snapshot["referral_orders"] > 0
    assert snapshot["net_revenue"] == snapshot["gross_revenue"] - snapshot["refund_total"]


def test_inventory_read_model(client):
    inventory = client.get("/inventory").json()
    assert len(inventory["products"]) == 10
    assert inventory["reservations"] == []
    assert {"sku", "unit_price", "stock", "inventory_value"} <= set(inventory["products"][0])


def test_auth_can_be_disabled_for_scripts():
    with TestClient(create_app(build_system(), require_auth=False)) as c:
        assert c.get("/inventory").status_code == 200
        assert c.get("/finance").status_code == 200


# --------------------------------------------------------------------------- #
# Document intake
# --------------------------------------------------------------------------- #

RETURN_SLIP = Path(__file__).parent / "fixtures" / "documents" / "return_slip.pdf"


def _document_body(path: Path, filename: str | None = None) -> dict[str, str]:
    return {
        "filename": filename or path.name,
        "content_base64": base64.b64encode(path.read_bytes()).decode(),
    }


def test_document_upload_answers_and_fill(client):
    response = client.post("/documents", json=_document_body(RETURN_SLIP))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["doc_type"] == "return_slip"
    assert body["blocking"] == ["sku"]
    assert {row["key"] for row in body["rows"]} >= {"sku", "refund_total", "order_id"}
    job_id = body["id"]

    assert [d["id"] for d in client.get("/documents").json()["documents"]] == [job_id]

    answered = client.post(
        f"/documents/{job_id}/answers", json={"answers": {"sku": "SKU-1001"}}
    )
    assert answered.status_code == 200, answered.text
    assert answered.json()["updated"] == ["sku"]
    assert answered.json()["blocking"] == []

    filled = client.post(f"/documents/{job_id}/fill", json={"mode": "original"})
    assert filled.status_code == 200, filled.text
    assert filled.headers["content-type"].startswith("application/pdf")
    assert filled.content.startswith(b"%PDF")

    assert client.delete(f"/documents/{job_id}").status_code == 204
    assert client.get(f"/documents/{job_id}").status_code == 404


def test_document_api_rejects_bad_input(client):
    assert (
        client.post(
            "/documents", json={"filename": "x.pdf", "content_base64": "not-base64!"}
        ).status_code
        == 400
    )
    bad_type = {
        "filename": "notes.txt",
        "content_base64": base64.b64encode(b"hello").decode(),
    }
    assert client.post("/documents", json=bad_type).status_code == 400
    assert client.get("/documents/unknown").status_code == 404
    assert client.post("/documents/unknown/answers", json={"answers": {}}).status_code == 404
    assert (
        client.post(
            "/documents", json=_document_body(RETURN_SLIP)
        ).json()["id"]
    )
    job_id = client.get("/documents").json()["documents"][0]["id"]
    assert (
        client.post(f"/documents/{job_id}/fill", json={"mode": "sideways"}).status_code
        == 400
    )

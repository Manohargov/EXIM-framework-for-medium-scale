"""Event-derived metrics and Prometheus rendering."""

from ecomops.metrics import prometheus, snapshot
from ecomops.system import build_system


async def test_snapshot_counts_events_and_approvals():
    system = build_system()
    await system.receive_customer_request("CUST-M", "2 wireless bluetooth earbuds to Berlin")

    snap = snapshot(system.bus, system.inventory)
    assert snap["events"] == len(system.bus.history)
    assert snap["reservations"] == 1
    assert snap["shipments_routed"] == 1
    assert snap["pending_approvals"] == 1        # the 30% markdown needs a human
    assert snap["products"] == 10

    text = prometheus(snap)
    assert f"ecomops_events_total {snap['events']}" in text
    assert 'ecomops_events_by_topic_total{topic="CustomerRequestReceived"} 1' in text
    assert "ecomops_pending_approvals 1" in text


async def test_refund_total_is_a_gauge():
    system = build_system()
    await system.receive_customer_request("CUST-M2", "1 wireless bluetooth earbuds")
    for approval in system.pending_approvals:
        await system.decide(approval.proposal_id, granted=True, decided_by="tester")

    await system.receive_customer_request(
        "CUST-M2", "I want a refund for the wireless bluetooth earbuds, damaged"
    )
    for approval in system.pending_approvals:
        await system.decide(approval.proposal_id, granted=True, decided_by="tester")

    snap = system.metrics()
    assert snap["refunds_issued"] == 1
    assert snap["refund_amount_total"] > 0
    assert "ecomops_refund_amount_total" in prometheus(snap)

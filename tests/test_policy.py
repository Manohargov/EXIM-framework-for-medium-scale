"""Approval-gate policy: what runs autonomously, what waits for a human."""

import itertools

from ecomops.bus import EventBus
from ecomops.events import ActionProposed, RiskLevel
from ecomops.policy import ApprovalGate, PolicyConfig

_seq = itertools.count(1)


def _proposal(bus: EventBus, action: str, parameters: dict, value: float = 0.0) -> ActionProposed:
    p = ActionProposed(
        proposal_id=f"p-{next(_seq)}",
        proposing_agent="test",
        action=action,
        parameters=parameters,
        reason="test",
        estimated_value=value,
        source="test",
    )
    bus.publish(p)
    return p


async def _run_gate(config: PolicyConfig | None = None) -> tuple[EventBus, ApprovalGate]:
    bus = EventBus()
    gate = ApprovalGate(bus, config or PolicyConfig())
    gate.attach()
    return bus, gate


async def test_small_discount_runs_autonomously():
    bus, _gate = await _run_gate()
    _proposal(bus, "apply_discount", {"discount_pct": 10.0, "sku": "SKU-1"}, value=50.0)
    await bus.drain()

    assert len(bus.of_type("ActionApproved")) == 1
    assert not bus.of_type("ApprovalRequested")


async def test_large_discount_requires_human():
    bus, _gate = await _run_gate()
    _proposal(bus, "apply_discount", {"discount_pct": 35.0, "sku": "SKU-1"}, value=50.0)
    await bus.drain()

    reqs = bus.of_type("ApprovalRequested")
    assert len(reqs) == 1
    assert reqs[0].risk == RiskLevel.MEDIUM
    assert "exceeds" in reqs[0].reason
    assert not bus.of_type("ActionApproved")


async def test_small_reservation_autonomous_large_gated():
    bus, _gate = await _run_gate()
    _proposal(bus, "reserve_stock", {"sku": "S", "quantity": 3, "customer_id": "C"})
    _proposal(bus, "reserve_stock", {"sku": "S", "quantity": 30, "customer_id": "C"})
    await bus.drain()

    assert len(bus.of_type("ActionApproved")) == 1
    assert len(bus.of_type("ApprovalRequested")) == 1


async def test_refund_always_human():
    bus, _gate = await _run_gate()
    _proposal(bus, "resolve_exception", {"resolution_action": "refund", "cost": 5.0})
    await bus.drain()

    assert len(bus.of_type("ApprovalRequested")) == 1
    assert bus.of_type("ApprovalRequested")[0].risk == RiskLevel.HIGH


async def test_return_refund_always_human_and_cheap_replacement_autonomous():
    bus, _gate = await _run_gate()
    _proposal(bus, "issue_refund", {"amount": 12.0, "sku": "S", "quantity": 1})
    _proposal(bus, "authorise_replacement", {"value": 12.0, "sku": "S", "quantity": 1})
    await bus.drain()

    assert len(bus.of_type("ApprovalRequested")) == 1
    assert bus.of_type("ApprovalRequested")[0].action == "issue_refund"
    approved = bus.of_type("ActionApproved")
    assert len(approved) == 1 and approved[0].action == "authorise_replacement"


async def test_expensive_replacement_is_gated():
    bus, _gate = await _run_gate()
    _proposal(bus, "authorise_replacement", {"value": 500.0, "sku": "S", "quantity": 1})
    await bus.drain()
    assert len(bus.of_type("ApprovalRequested")) == 1


async def test_cheap_reroute_autonomous():
    bus, _gate = await _run_gate()
    _proposal(bus, "resolve_exception", {"resolution_action": "reroute", "cost": 8.0})
    await bus.drain()

    assert len(bus.of_type("ActionApproved")) == 1


async def test_expensive_reroute_gated():
    bus, _gate = await _run_gate()
    _proposal(bus, "resolve_exception", {"resolution_action": "reroute", "cost": 75.0})
    await bus.drain()

    assert len(bus.of_type("ApprovalRequested")) == 1


async def test_human_grant_executes_and_denial_rejects():
    from ecomops.policy import decide

    bus, _gate = await _run_gate()
    p = _proposal(bus, "apply_discount", {"discount_pct": 40.0, "sku": "S"})
    await bus.drain()
    assert bus.pending_approvals, "should be waiting for a human"

    decide(bus, p.proposal_id, granted=True, decided_by="ops-lead")
    await bus.drain()
    approved = bus.of_type("ActionApproved")
    assert len(approved) == 1 and approved[0].mode == "human" and approved[0].decided_by == "ops-lead"

    # A second proposal, denied this time.
    p2 = _proposal(bus, "apply_discount", {"discount_pct": 40.0, "sku": "S2"})
    await bus.drain()
    decide(bus, p2.proposal_id, granted=False, decided_by="ops-lead", comment="not this quarter")
    await bus.drain()


    rejected = bus.of_type("ActionRejected")
    assert len(rejected) == 1 and rejected[0].reason == "not this quarter"
    # only the granted one produced an approval
    assert len(bus.of_type("ActionApproved")) == 1


async def test_unknown_action_escalates_not_executes():
    bus, _gate = await _run_gate()
    _proposal(bus, "drop_database", {"sku": "S"})
    await bus.drain()


    assert bus.of_type("EscalationRaised") or bus.of_type("ApprovalRequested")
    assert not bus.of_type("ActionApproved")


async def test_policy_config_is_the_single_knob():
    bus, _gate = await _run_gate(PolicyConfig(max_autonomous_discount_pct=50.0))
    _proposal(bus, "apply_discount", {"discount_pct": 45.0, "sku": "S"}, value=10.0)
    await bus.drain()

    assert len(bus.of_type("ActionApproved")) == 1   # 45% now under the raised limit

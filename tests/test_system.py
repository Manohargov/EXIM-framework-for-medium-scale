"""End-to-end flows through the assembled system (rule-based backend)."""

import pytest

from ecomops.llm import LLMUnavailable, TokenOverlapEmbedder
from ecomops.rules import build_rule_llm
from ecomops.system import build_system


@pytest.fixture
def system():
    return build_system()   # rule-based LLM, token-overlap embedder (offline)


async def test_purchase_request_flows_to_shipment(system):
    await system.receive_customer_request(
        "CUST-001", "I need 2 wireless bluetooth earbuds delivered to Berlin ASAP"
    )
    # allow the human-in-the-loop step
    for e in system.pending_approvals:
        await system.decide(e.proposal_id, granted=True, decided_by="tester")

    intent = system.bus.of_type("CustomerIntentCaptured")[0]
    assert intent.intent.intent_type.value == "purchase"
    assert intent.intent.items[0].sku is not None
    assert intent.matched is True

    signals = system.bus.of_type("DemandSignal")
    assert len(signals) == 1 and signals[0].sku.startswith("SKU-")

    strategy = system.bus.of_type("ClearanceStrategyProposed")
    assert strategy, "clearance agent should propose a strategy"

    reserved = system.bus.of_type("StockReserved")
    assert len(reserved) == 1 and reserved[0].quantity == 2

    routed = system.bus.of_type("ShipmentRouted")
    assert len(routed) == 1
    assert routed[0].destination == "Berlin"
    assert routed[0].plan.eta_days <= 2.0        # "ASAP" -> express lane


async def test_ageing_stock_gets_discounted(system):
    # SKU-1003 (laptop stand) is 350 days old with 60 on hand
    await system.receive_customer_request("CUST-002", "one aluminium laptop stand")
    for e in system.pending_approvals:
        await system.decide(e.proposal_id, granted=True, decided_by="tester")

    strategy = system.bus.of_type("ClearanceStrategyProposed")[0]
    assert strategy.strategy.discount_pct > 0

    applied = system.bus.of_type("DiscountApplied")
    assert len(applied) == 1 and applied[0].new_price < applied[0].previous_price


async def test_unmatched_product_triggers_clarification(system):
    await system.receive_customer_request("CUST-003", "do you sell a quantum flux capacitor")
    await system.bus.drain()

    clarifications = system.bus.of_type("IntentNeedsClarification")
    assert clarifications, "ambiguous request must escalate, not guess"
    assert "quantum flux capacitor" in clarifications[0].question
    # nothing should have been reserved or routed
    assert not system.bus.of_type("StockReserved")
    assert not system.bus.of_type("ShipmentRouted")


async def test_insufficient_stock_fails_loudly(system):
    # SKU-1005 has 15 units; ask for 40 -> above reservation limit -> human gate,
    # and even granted, the reservation cannot succeed
    await system.receive_customer_request("CUST-004", "40 mechanical keyboards please")
    for e in system.pending_approvals:
        await system.decide(e.proposal_id, granted=True, decided_by="tester")

    failed = system.bus.of_type("StockReservationFailed")
    assert len(failed) == 1
    assert failed[0].requested == 40 and failed[0].available == 15
    assert not system.bus.of_type("ShipmentRouted")


async def test_proactive_overstock_clearance(system):
    await system.report_stock_levels()
    for e in system.pending_approvals:
        await system.decide(e.proposal_id, granted=True, decided_by="tester")

    strategies = system.bus.of_type("ClearanceStrategyProposed")
    assert strategies and all(s.trigger == "overstock" for s in strategies)
    # SKU-1008 (hdmi, 400 days) and SKU-1001 (earbuds, 210 days, 240 on hand) qualify
    skus = {s.strategy.sku for s in strategies}
    assert {"SKU-1001", "SKU-1008"} <= skus


async def test_delivery_exception_reroute_autonomous(system):
    await system.receive_customer_request("CUST-005", "1 usb-c fast charging cable")
    routed = system.bus.of_type("ShipmentRouted")
    assert routed, "shipment should be routed (cheap standard lane is autonomous)"

    await system.report_delivery_exception(routed[0].shipment_id, "carrier_delay", "hub strike")

    proposed = system.bus.of_type("ExceptionResolutionProposed")
    assert proposed and proposed[0].resolution.action.value == "reroute"

    resolved = system.bus.of_type("DeliveryExceptionResolved")
    assert len(resolved) == 1
    assert resolved[0].decided_by == "policy"    # cheap reroute: no human needed


async def test_damaged_parcel_replacement_needs_approval(system):
    await system.receive_customer_request("CUST-006", "2 insulated water bottle 750ml")
    routed = system.bus.of_type("ShipmentRouted")
    await system.report_delivery_exception(routed[0].shipment_id, "damaged", "crushed in transit")

    pending = [e for e in system.pending_approvals if e.action == "resolve_exception"]
    assert pending, "a replacement must wait for a human decision"

    await system.decide(pending[0].proposal_id, granted=True, decided_by="ops-lead")
    resolved = system.bus.of_type("DeliveryExceptionResolved")
    assert len(resolved) == 1
    assert resolved[0].resolution.action.value == "replace"
    assert resolved[0].decided_by == "ops-lead"


async def test_denied_approval_escalates(system):
    # 15 units > autonomous reservation limit (10) -> human gate; denying it must escalate
    await system.receive_customer_request("CUST-007", "15 noise cancelling headphones")
    for e in system.pending_approvals:
        await system.decide(e.proposal_id, granted=False, decided_by="ops-lead", comment="budget freeze")


    escalations = system.bus.of_type("EscalationRaised")
    assert escalations
    assert not system.bus.of_type("StockReserved")
    assert not system.bus.of_type("ShipmentRouted")


async def test_damaged_return_refund_requires_human(system):
    await system.receive_customer_request("CUST-010", "1 wireless bluetooth earbuds to Berlin")
    for e in system.pending_approvals:
        await system.decide(e.proposal_id, granted=True, decided_by="tester")
    assert system.bus.of_type("ShipmentRouted")

    stock_before = system.inventory.products["SKU-1001"].stock
    await system.receive_customer_request(
        "CUST-010", "I want a refund for the wireless bluetooth earbuds, they arrived damaged"
    )

    assessed = system.bus.of_type("ReturnAssessed")
    assert assessed and assessed[0].assessment.action.value == "refund"

    pending = [e for e in system.pending_approvals if e.action == "issue_refund"]
    assert pending, "refunds must wait for a human decision"
    assert not system.bus.of_type("RefundIssued")

    await system.decide(pending[0].proposal_id, granted=True, decided_by="ops-lead")
    refunded = system.bus.of_type("RefundIssued")
    assert len(refunded) == 1 and refunded[0].decided_by == "ops-lead"
    # refund uses the price at dispatch, even though a markdown was approved later
    assert refunded[0].amount == 59.99
    # damaged goods are not put back on the shelf
    assert refunded[0].restocked is False
    assert system.inventory.products["SKU-1001"].stock == stock_before


async def test_undamaged_return_restocks_the_item(system):
    stock_before = system.inventory.products["SKU-1001"].stock
    await system.receive_customer_request("CUST-014", "1 wireless bluetooth earbuds")
    for e in system.pending_approvals:
        await system.decide(e.proposal_id, granted=True, decided_by="tester")

    await system.receive_customer_request(
        "CUST-014", "I changed my mind, I want a refund for the wireless bluetooth earbuds"
    )
    for e in system.pending_approvals:
        await system.decide(e.proposal_id, granted=True, decided_by="tester")

    refunded = system.bus.of_type("RefundIssued")[0]
    assert refunded.restocked is True
    assert system.inventory.products["SKU-1001"].stock == stock_before


async def test_denied_refund_emits_return_rejected(system):
    await system.receive_customer_request("CUST-013", "1 wireless bluetooth earbuds")
    for e in system.pending_approvals:
        await system.decide(e.proposal_id, granted=True, decided_by="tester")

    await system.receive_customer_request(
        "CUST-013", "I want a refund for the wireless bluetooth earbuds, damaged"
    )
    pending = [e for e in system.pending_approvals if e.action == "issue_refund"]
    assert pending
    await system.decide(
        pending[0].proposal_id, granted=False, decided_by="ops-lead", comment="no photo evidence"
    )

    rejected = system.bus.of_type("ReturnRejected")
    assert len(rejected) == 1
    assert rejected[0].decided_by == "ops-lead"
    assert "no photo evidence" in rejected[0].reason
    assert not system.bus.of_type("RefundIssued")


async def test_return_without_delivery_asks_for_order(system):
    await system.receive_customer_request("CUST-011", "I want a refund for a desk lamp led")
    assert system.bus.of_type("ReturnNeedsOrder")
    assert not system.bus.of_type("ReturnRequested")


async def test_replacement_within_value_runs_autonomously(system):
    await system.receive_customer_request("CUST-012", "1 usb-c fast charging cable")
    assert system.bus.of_type("ShipmentRouted")

    await system.receive_customer_request(
        "CUST-012", "please replace the faulty usb-c fast charging cable"
    )
    assessed = system.bus.of_type("ReturnAssessed")
    assert assessed and assessed[0].assessment.action.value == "replacement"

    replacements = system.bus.of_type("ReplacementAuthorised")
    assert len(replacements) == 1
    assert replacements[0].decided_by == "policy"     # cheap replacement needs no human


async def test_every_conversation_shares_one_correlation_id(system):
    await system.receive_customer_request("CUST-008", "2 wireless bluetooth earbuds")
    for e in system.pending_approvals:
        await system.decide(e.proposal_id, granted=True, decided_by="tester")

    corr = system.bus.of_type("CustomerRequestReceived")[0].correlation_id
    routed = system.bus.of_type("ShipmentRouted")[0]
    assert routed.correlation_id == corr


async def test_mlx_unloadable_falls_back_to_rules():
    # Point MlxLLM at a model that cannot load (mlx-lm absent or repo missing):
    # the fallback path must engage and the flow must complete.
    from ecomops.llm import FallbackLLM, MlxLLM
    from ecomops.system import EcomopsSystem

    system = EcomopsSystem(
        llm=FallbackLLM(
            primary=MlxLLM(model="mlx-community/this-model-does-not-exist-xyz"),
            secondary=build_rule_llm(),
        ),
        embedder=TokenOverlapEmbedder(),
    )
    await system.receive_customer_request("CUST-009", "1 desk lamp led")
    captured = system.bus.of_type("CustomerIntentCaptured")[0]
    assert "fallback" in captured.llm_backend
    assert system.bus.of_type("StockReserved")


async def test_fallback_llm_wraps_backend_failure():
    from ecomops.llm import FallbackLLM

    class Boom:
        async def complete(self, **kwargs):
            raise LLMUnavailable("no server")

    llm = FallbackLLM(primary=Boom(), secondary=build_rule_llm())
    result = await llm.complete(task="capture_intent", instructions="", payload={"request_text": "1 mouse", "catalogue": []})
    assert result.backend.startswith("fallback:")
    assert result.data["intent_type"] in ("purchase", "other")


async def test_embedder_fallback_is_pure_python():
    emb = TokenOverlapEmbedder()
    assert emb.similarity("wireless bluetooth earbuds", "bluetooth earbuds case") > 0.3
    assert emb.similarity("earbuds", "zebra") == 0.0

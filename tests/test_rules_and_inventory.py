"""Rule handlers and inventory behaviour."""

from ecomops.inventory import Inventory
from ecomops.llm import TokenOverlapEmbedder
from ecomops.rules import (
    assess_return,
    build_rule_llm,
    capture_intent,
    develop_clearance_strategy,
    plan_route,
    resolve_exception,
)

# --------------------------------------------------------------------------- rules

def test_intent_purchase_with_quantity():
    out = capture_intent({"request_text": "I want 3 hdmi cable 2m to Lyon", "catalogue": ["hdmi cable 2m"]})
    assert out["intent_type"] == "purchase"
    assert out["items"][0]["quantity"] == 3
    assert out["destination"] == "Lyon"


def test_intent_bulk_order_detected():
    out = capture_intent({"request_text": "wholesale enquiry: 5 pallets of usb-c fast charging cable", "catalogue": []})
    assert out["intent_type"] == "bulk_order"
    assert out["urgency"] == "high"


def test_intent_delivery_status():
    out = capture_intent({"request_text": "where is my delivery? tracking please", "catalogue": []})
    assert out["intent_type"] == "delivery_status"


def test_intent_return():
    out = capture_intent({"request_text": "I want to return the broken mouse", "catalogue": []})
    assert out["intent_type"] == "return"


def test_strategy_discounts_ageing_stock():
    out = develop_clearance_strategy({
        "sku": "SKU-X", "quantity": 1,
        "stock": {"on_hand": 60, "age_days": 350, "unit_price": 34.5},
        "substitutes": [{"sku": "SKU-A"}, {"sku": "SKU-B"}],
    })
    assert out["discount_pct"] >= 20.0
    assert out["substitute_skus"] == ["SKU-A", "SKU-B"]


def test_strategy_no_discount_on_healthy_stock():
    out = develop_clearance_strategy({
        "sku": "SKU-Y", "quantity": 1,
        "stock": {"on_hand": 30, "age_days": 10, "unit_price": 89.0},
        "substitutes": [],
    })
    assert out["discount_pct"] == 0.0


def test_route_high_urgency_is_express():
    out = plan_route({"urgency": "high", "quantity": 1, "destination": "Berlin"})
    assert out["carrier"] == "SwiftExpress"
    assert out["eta_days"] == 1.0


def test_route_bulk_is_freight():
    out = plan_route({"urgency": "normal", "quantity": 120, "destination": "domestic"})
    assert out["carrier"] == "EcoFreight"
    assert out["service_level"] == "pallet-freight"


def test_route_international():
    out = plan_route({"urgency": "normal", "quantity": 2, "destination": "overseas"})
    assert out["carrier"] == "AirNexus"


def test_resolution_by_exception_type():
    assert resolve_exception({"exception": "damaged", "plan": {}})["action"] == "replace"
    assert resolve_exception({"exception": "lost", "plan": {}, "order_value": 42.0})["action"] == "refund"
    assert resolve_exception({"exception": "wrong_address", "plan": {}})["action"] == "reroute"
    assert resolve_exception({"exception": "carrier_delay", "plan": {}})["action"] == "reroute"


def test_assess_return_refund_replacement_and_window():
    refund = assess_return(
        {"reason": "it arrived damaged, I want a refund", "quantity": 1, "order_value": 59.99,
         "days_since_delivery": 5, "policy": {"window_days": 30}}
    )
    assert refund["action"] == "refund" and refund["eligible"] is True
    assert refund["refund_amount"] == 59.99

    replacement = assess_return(
        {"reason": "please replace this faulty unit", "quantity": 1, "order_value": 20.0,
         "days_since_delivery": 5, "policy": {"window_days": 30}}
    )
    assert replacement["action"] == "replacement"

    late = assess_return(
        {"reason": "I changed my mind", "quantity": 1, "order_value": 20.0,
         "days_since_delivery": 90, "policy": {"window_days": 30}}
    )
    assert late["action"] == "reject" and late["eligible"] is False


def test_rule_llm_registry():
    llm = build_rule_llm()
    assert set(llm.rules) == {
        "capture_intent",
        "extract_tasks",
        "develop_clearance_strategy",
        "plan_route",
        "resolve_exception",
        "assess_return",
        "write_advert_copy",
        "write_transit_copy",
    }


# --------------------------------------------------------------------------- inventory

def test_catalogue_and_matching():
    inv = Inventory.with_default_catalogue(TokenOverlapEmbedder())
    product, score = inv.match("wireless bluetooth earbuds")
    assert product is not None and product.sku == "SKU-1001"
    assert score > 0.5


def test_inventories_do_not_share_mutable_products():
    a = Inventory.with_default_catalogue(TokenOverlapEmbedder())
    b = Inventory.with_default_catalogue(TokenOverlapEmbedder())
    a.apply_discount("SKU-1001", 50.0)
    a.products["SKU-1001"].stock = 0

    assert b.products["SKU-1001"].unit_price == 59.99
    assert b.products["SKU-1001"].stock == 240


def test_match_below_threshold_returns_none():
    inv = Inventory.with_default_catalogue(TokenOverlapEmbedder())
    product, _ = inv.match("quantum flux capacitor")
    assert product is None


def test_reserve_decrements_and_release_restores():
    inv = Inventory.with_default_catalogue(TokenOverlapEmbedder())
    before = inv.products["SKU-1005"].stock
    res = inv.reserve("SKU-1005", 5, "C1")
    assert res is not None and inv.products["SKU-1005"].stock == before - 5
    inv.release(res.reservation_id)
    assert inv.products["SKU-1005"].stock == before


def test_reserve_more_than_stock_returns_none():
    inv = Inventory.with_default_catalogue(TokenOverlapEmbedder())
    assert inv.reserve("SKU-1005", 9999, "C1") is None


def test_apply_discount():
    inv = Inventory.with_default_catalogue(TokenOverlapEmbedder())
    prev, new = inv.apply_discount("SKU-1001", 30.0)
    assert new == round(prev * 0.7, 2)


def test_substitutes_same_category_only():
    inv = Inventory.with_default_catalogue(TokenOverlapEmbedder())
    subs = inv.substitutes_for(inv.products["SKU-1007"])   # headphones
    assert subs and all(s.category == "electronics" for s in subs)
    assert all(s.sku != "SKU-1007" for s in subs)


def test_overstocked_selection():
    inv = Inventory.with_default_catalogue(TokenOverlapEmbedder())
    skus = {p.sku for p in inv.overstocked()}
    assert "SKU-1001" in skus    # 210 days old
    assert "SKU-1008" in skus    # 400 days old
    assert "SKU-1005" not in skus  # 20 days, 15 units — healthy

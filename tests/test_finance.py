"""Finance and referral read models."""

import pytest

from ecomops.finance import (
    REFERRAL_PARTNERS,
    finance_snapshot,
    finance_summary_line,
    referral_rows,
)
from ecomops.system import build_system


@pytest.fixture
def system():
    return build_system()


def test_referral_ledger_is_deterministic_and_complete():
    rows = referral_rows()
    assert len(rows) == len(REFERRAL_PARTNERS)
    assert [row.as_dict() for row in rows] == [row.as_dict() for row in referral_rows()]

    assert rows == sorted(rows, key=lambda row: row.revenue, reverse=True)
    assert all(row.orders > 0 and row.revenue > 0 for row in rows)
    assert all(row.status in {"paid", "pending"} for row in rows)
    rates = {partner.name: partner.commission_pct for partner in REFERRAL_PARTNERS}
    for row in rows:
        assert row.commission == pytest.approx(row.revenue * rates[row.partner] / 100.0, abs=0.01)


def test_referral_seed_changes_the_ledger():
    assert [row.as_dict() for row in referral_rows("seed-a")] != [
        row.as_dict() for row in referral_rows("seed-b")
    ]


def test_empty_system_finance_snapshot(system):
    snapshot = finance_snapshot(system)
    assert snapshot["gross_revenue"] == 0
    assert snapshot["refunds"] == 0
    assert snapshot["net_revenue"] == 0
    assert snapshot["inventory_value"] > 0
    assert snapshot["referrals"]
    assert snapshot["referral_revenue"] > 0
    assert snapshot["commission_due"] + snapshot["commission_paid"] == pytest.approx(
        sum(row["commission"] for row in snapshot["referrals"]), abs=0.02
    )


async def test_revenue_and_markdowns_are_event_derived(system):
    await system.receive_customer_request(
        "CUST-FIN", "2 wireless bluetooth earbuds delivered to Berlin ASAP"
    )
    for event in list(system.pending_approvals):
        await system.decide(event.proposal_id, granted=True, decided_by="tester")

    snapshot = finance_snapshot(system)
    reservations = system.bus.of_type("StockReserved")
    assert snapshot["orders"] == len(reservations) == 1
    expected = sum(
        reservation.quantity * system.inventory.products[reservation.sku].unit_price
        for reservation in reservations
    )
    assert snapshot["gross_revenue"] == pytest.approx(expected, abs=0.01)
    assert snapshot["net_revenue"] == snapshot["gross_revenue"]
    assert snapshot["discounts"] == len(system.bus.of_type("DiscountApplied"))
    assert snapshot["markdown_value"] >= 0


async def test_refunds_reduce_net_revenue(system):
    await system.receive_customer_request(
        "CUST-FIN2", "1 wireless bluetooth earbuds delivered to Berlin"
    )
    for event in list(system.pending_approvals):
        await system.decide(event.proposal_id, granted=True, decided_by="tester")

    before = finance_snapshot(system)
    await system.receive_customer_request(
        "CUST-FIN2", "I want a refund for the wireless bluetooth earbuds, damaged"
    )
    for event in list(system.pending_approvals):
        await system.decide(event.proposal_id, granted=True, decided_by="tester")

    after = finance_snapshot(system)
    assert after["refunds"] == 1
    assert after["refund_total"] > 0
    assert after["net_revenue"] == pytest.approx(before["net_revenue"] - after["refund_total"], abs=0.01)


def test_summary_line_mentions_the_headline_numbers(system):
    line = finance_summary_line(finance_snapshot(system))
    assert line.startswith("gross $")
    assert "refunds $" in line and "net $" in line and "referral revenue $" in line

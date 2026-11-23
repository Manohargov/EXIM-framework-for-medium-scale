"""Stock Clearance Agent.

Responsibility: connect demand to inventory. For each demand signal it looks at
the stock position (on-hand, ageing, price) and asks the LLM for a clearance
strategy — discount, bundling, substitution — then proposes the actions:

* ``apply_discount``     — markdown to move ageing/overstocked lines (policy-gated);
* ``reserve_stock``      — hold units for the customer (policy-gated on quantity).

It also works *proactively*: a ``StockLevelReport`` triggers clearance
strategies for overstocked SKUs even with no demand attached.

Substitution uses the embedder: when the requested SKU is out of stock, the
nearest in-category products are proposed as substitutes rather than failing.
"""

from __future__ import annotations

from ..events import (
    ClearanceStrategy,
    ClearanceStrategyProposed,
    DemandSignal,
    DiscountApplied,
    Event,
    StockLevelReport,
    StockReservationFailed,
    StockReserved,
)
from ..inventory import Inventory
from .base import Agent


class StockClearanceAgent(Agent):
    name = "stock-clearance"

    def __init__(self, bus, llm, inventory: Inventory) -> None:
        super().__init__(bus, llm)
        self.inventory = inventory

    def attach(self) -> None:
        self.bus.subscribe(
            self.name, self.handle, "DemandSignal", "StockLevelReport",
            "ActionApproved", "ActionRejected",
        )

    # ---------------------------------------------------------------- react

    async def react(self, event: Event) -> None:
        if isinstance(event, DemandSignal):
            await self._on_demand(event)
        elif isinstance(event, StockLevelReport):
            await self._on_report(event)

    async def _on_demand(self, signal: DemandSignal) -> None:
        product = self.inventory.products[signal.sku]
        payload = {
            "sku": signal.sku,
            "product_name": product.name,
            "quantity": signal.quantity,
            "urgency": signal.urgency.value,
            "stock": {
                "on_hand": product.stock,
                "age_days": product.age_days,
                "unit_price": product.unit_price,
            },
            "substitutes": [
                {"sku": p.sku, "name": p.name, "stock": p.stock}
                for p in self.inventory.substitutes_for(product)
            ],
        }
        result = await self.ask(
            "develop_clearance_strategy",
            "Given the stock position and the demand, propose a clearance strategy as "
            "JSON: {sku, discount_pct, bundle_with, substitute_skus, rationale}. "
            "Discount ageing or overstocked lines; never discount healthy stock.",
            payload,
            schema=ClearanceStrategy.model_json_schema(),
        )
        strategy = ClearanceStrategy.model_validate(result.data)

        self.bus.publish(
            ClearanceStrategyProposed(
                strategy=strategy,
                trigger="demand",
                llm_backend=result.backend,
                source=self.name,
            )
        )

        # 1) reserve the requested units (gated on quantity)
        self.propose(
            "reserve_stock",
            {"sku": signal.sku, "quantity": signal.quantity, "customer_id": signal.customer_id},
            reason=f"hold {signal.quantity} x {product.name} for customer {signal.customer_id}",
            estimated_value=round(signal.quantity * product.unit_price, 2),
        )
        # 2) apply the markdown if the strategy asks for one (gated on pct)
        if strategy.discount_pct > 0:
            value = round(product.stock * product.unit_price * strategy.discount_pct / 100.0, 2)
            self.propose(
                "apply_discount",
                {"sku": signal.sku, "discount_pct": strategy.discount_pct},
                reason=strategy.rationale,
                estimated_value=value,
            )

    async def _on_report(self, report: StockLevelReport) -> None:
        for sku in report.overstocked:
            product = self.inventory.products[sku]
            payload = {
                "sku": sku,
                "product_name": product.name,
                "quantity": 0,
                "urgency": "normal",
                "stock": {"on_hand": product.stock, "age_days": product.age_days, "unit_price": product.unit_price},
                "substitutes": [
                    {"sku": p.sku, "name": p.name, "stock": p.stock}
                    for p in self.inventory.substitutes_for(product)
                ],
            }
            result = await self.ask(
                "develop_clearance_strategy",
                "Proactive clearance: propose a strategy to move ageing/overstocked lines.",
                payload,
                schema=ClearanceStrategy.model_json_schema(),
            )
            strategy = ClearanceStrategy.model_validate(result.data)
            self.bus.publish(
                ClearanceStrategyProposed(
                    strategy=strategy,
                    trigger="overstock",
                    llm_backend=result.backend,
                    source=self.name,
                )
            )
            if strategy.discount_pct > 0:
                value = round(product.stock * product.unit_price * strategy.discount_pct / 100.0, 2)
                self.propose(
                    "apply_discount",
                    {"sku": sku, "discount_pct": strategy.discount_pct},
                    reason=strategy.rationale,
                    estimated_value=value,
                )

    # ------------------------------------------------------------------ act

    async def act(self, proposal_id: str, action: str, parameters) -> None:
        if action == "reserve_stock":
            sku = parameters["sku"]
            qty = int(parameters["quantity"])
            customer_id = parameters["customer_id"]
            reservation = self.inventory.reserve(sku, qty, customer_id)
            if reservation is None:
                available = self.inventory.products[sku].stock
                self.bus.publish(
                    StockReservationFailed(
                        sku=sku, requested=qty, available=available, customer_id=customer_id,
                        source=self.name,
                    )
                )
                # Offer a substitute instead of failing silently.
                product = self.inventory.products[sku]
                subs = self.inventory.substitutes_for(product)
                if subs:
                    self.log.info("offering substitutes %s for %s", [s.sku for s in subs], sku)
                return
            self.bus.publish(
                StockReserved(
                    sku=sku, quantity=qty, customer_id=customer_id,
                    order_id=reservation.order_id, source=self.name,
                )
            )
        elif action == "apply_discount":
            sku = parameters["sku"]
            pct = float(parameters["discount_pct"])
            previous, new = self.inventory.apply_discount(sku, pct)
            self.bus.publish(
                DiscountApplied(
                    sku=sku, discount_pct=pct, previous_price=previous, new_price=new,
                    source=self.name,
                )
            )

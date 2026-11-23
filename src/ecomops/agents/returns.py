"""Returns & Refunds Agent.

Responsibility: close the loop the other three agents leave open. A customer can
already *ask* for a refund or replacement — the Customer Requirements Agent
captures that intent — but until now nothing acted on it. This agent matches the
request to a delivered shipment, asks the model (or the rules) to assess
eligibility, and proposes the compensating action:

* ``issue_refund``           — money back; always human-gated by default;
* ``authorise_replacement``  — send another unit; gated on value.

The agent is event-driven like the rest: it learns about deliveries from
``ShipmentRouted`` rather than reading another agent's tables, and it never
issues a refund directly. Denials and ineligible returns produce an explicit
``ReturnRejected`` instead of silently doing nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..events import (
    CustomerIntentCaptured,
    Event,
    RefundIssued,
    ReplacementAuthorised,
    ReturnAction,
    ReturnAssessed,
    ReturnAssessment,
    ReturnNeedsOrder,
    ReturnRejected,
    ReturnRequested,
    ShipmentRouted,
    utcnow,
)
from ..inventory import Inventory
from .base import Agent


@dataclass
class ReturnPolicy:
    """Return-window rules. A client rollout would source these from its own policy."""

    window_days: int = 30
    damaged_always_eligible: bool = True
    restocking_fee_pct: float = 0.0


_DAMAGED = ("damaged", "broken", "defective", "faulty", "cracked", "unusable", "not working")
_NOT_RESALEABLE = (*_DAMAGED, "lost", "never received", "missing")


class ReturnsAgent(Agent):
    name = "returns"

    def __init__(
        self,
        bus,
        llm,
        inventory: Inventory,
        *,
        policy: ReturnPolicy | None = None,
    ) -> None:
        super().__init__(bus, llm)
        self.inventory = inventory
        self.policy = policy or ReturnPolicy()
        self._delivered: dict[tuple[str, str], list[dict]] = {}
        self._claimed: set[str] = set()                  # shipment ids already in a proposal
        self._proposals: dict[str, str] = {}             # proposal id -> shipment id

    def attach(self) -> None:
        self.bus.subscribe(
            self.name,
            self.handle,
            "CustomerIntentCaptured",
            "ShipmentRouted",
            "ActionApproved",
            "ActionRejected",
        )

    # ---------------------------------------------------------------- react

    async def react(self, event: Event) -> None:
        if isinstance(event, ShipmentRouted):
            self._remember(event)
        elif isinstance(event, CustomerIntentCaptured):
            await self._on_intent(event)

    def _remember(self, event: ShipmentRouted) -> None:
        self._delivered.setdefault((event.customer_id, event.sku), []).append(
            {
                "shipment_id": event.shipment_id,
                "order_id": event.order_id,
                "quantity": event.quantity,
                "destination": event.destination,
                "routed_at": event.ts,
                "customer_id": event.customer_id,
                "sku": event.sku,
                "order_value": event.order_value,
            }
        )

    async def _on_intent(self, event: CustomerIntentCaptured) -> None:
        if event.intent.intent_type.value not in ("return", "complaint"):
            return

        if not event.intent.items:
            self.bus.publish(
                ReturnNeedsOrder(
                    customer_id=event.customer_id,
                    sku=None,
                    question=(
                        "Which product and order would you like to return? "
                        "Please give the product name and your order id."
                    ),
                    source=self.name,
                )
            )
            return

        for item in event.intent.items:
            if item.sku is None:
                continue                      # customer agent already asked for clarification
            order = self._find_order(event.customer_id, item.sku)
            if order is None:
                self.bus.publish(
                    ReturnNeedsOrder(
                        customer_id=event.customer_id,
                        sku=item.sku,
                        question=(
                            f"We could not find a delivered {item.product_name or item.sku} "
                            f"for customer {event.customer_id}. Please provide the order id."
                        ),
                        source=self.name,
                    )
                )
                continue
            await self._assess(event, item, order)

    def _find_order(self, customer_id: str, sku: str) -> dict | None:
        for order in reversed(self._delivered.get((customer_id, sku), [])):
            if order["shipment_id"] not in self._claimed:
                return order
        return None

    async def _assess(self, event: CustomerIntentCaptured, item, order: dict) -> None:
        product = self.inventory.products[item.sku]
        # Refund the price the customer was charged at dispatch, not today's price.
        # A partial return is pro-rated by the units actually being returned.
        shipped_value = order.get("order_value") or order["quantity"] * product.unit_price
        unit_price = shipped_value / order["quantity"] if order["quantity"] else product.unit_price
        order_value = round(unit_price * item.quantity, 2)
        days = max(0, (utcnow() - order["routed_at"]).days)
        reason = event.request_text or event.intent.summary

        self.bus.publish(
            ReturnRequested(
                customer_id=event.customer_id,
                sku=item.sku,
                product_name=product.name,
                quantity=item.quantity,
                order_id=order["order_id"],
                reason=reason,
                source=self.name,
            )
        )

        payload = {
            "customer_id": event.customer_id,
            "sku": item.sku,
            "product_name": product.name,
            "quantity": item.quantity,
            "reason": reason,
            "order_value": order_value,
            "days_since_delivery": days,
            "policy": {
                "window_days": self.policy.window_days,
                "damaged_always_eligible": self.policy.damaged_always_eligible,
                "restocking_fee_pct": self.policy.restocking_fee_pct,
            },
        }
        result = await self.ask(
            "assess_return",
            "Assess this return request as JSON: {action (refund|replacement|reject), "
            "eligible, refund_amount, restocking_fee, rationale}. Respect the return "
            "window; damaged goods remain eligible; never invent an amount above the order value.",
            payload,
            schema=ReturnAssessment.model_json_schema(),
        )
        assessment = ReturnAssessment.model_validate(result.data)

        self.bus.publish(
            ReturnAssessed(
                customer_id=event.customer_id,
                sku=item.sku,
                quantity=item.quantity,
                order_id=order["order_id"],
                assessment=assessment,
                llm_backend=result.backend,
                source=self.name,
            )
        )

        if not assessment.eligible or assessment.action == ReturnAction.REJECT:
            self.bus.publish(
                ReturnRejected(
                    customer_id=event.customer_id,
                    order_id=order["order_id"],
                    sku=item.sku,
                    reason=assessment.rationale,
                    decided_by="policy",
                    source=self.name,
                )
            )
            return

        lower = reason.lower()
        restock = not any(w in lower for w in _NOT_RESALEABLE)
        common = {
            "customer_id": event.customer_id,
            "order_id": order["order_id"],
            "shipment_id": order["shipment_id"],
            "sku": item.sku,
            "quantity": item.quantity,
        }

        if assessment.action == ReturnAction.REFUND:
            amount = assessment.refund_amount or order_value
            proposal_id = self.propose(
                "issue_refund",
                {**common, "amount": amount, "restock": restock, "reason": reason},
                reason=assessment.rationale,
                estimated_value=amount,
            )
        else:
            proposal_id = self.propose(
                "authorise_replacement",
                {**common, "value": order_value, "reason": reason},
                reason=assessment.rationale,
                estimated_value=order_value,
            )

        self._claimed.add(order["shipment_id"])
        self._proposals[proposal_id] = order["shipment_id"]

    # ------------------------------------------------------------------ act

    async def act(self, proposal_id: str, action: str, parameters) -> None:
        decided_by = str(parameters.get("_approved_by", "policy"))
        if action == "issue_refund":
            restocked = False
            if parameters.get("restock"):
                self.inventory.restock(parameters["sku"], int(parameters["quantity"]))
                restocked = True
            self.bus.publish(
                RefundIssued(
                    customer_id=parameters["customer_id"],
                    order_id=parameters.get("order_id"),
                    sku=parameters["sku"],
                    quantity=int(parameters["quantity"]),
                    amount=float(parameters["amount"]),
                    restocked=restocked,
                    decided_by=decided_by,
                    source=self.name,
                )
            )
        elif action == "authorise_replacement":
            self.bus.publish(
                ReplacementAuthorised(
                    customer_id=parameters["customer_id"],
                    order_id=parameters.get("order_id"),
                    sku=parameters["sku"],
                    quantity=int(parameters["quantity"]),
                    decided_by=decided_by,
                    source=self.name,
                )
            )
        self._proposals.pop(proposal_id, None)

    async def on_rejected(self, event) -> None:
        """A denied refund/replacement is a return decision, not a generic escalation."""
        self._pending.pop(event.proposal_id, None)
        shipment_id = self._proposals.pop(event.proposal_id, None)
        if shipment_id is not None:
            self._claimed.discard(shipment_id)

        proposal = next(
            (e for e in self.bus.of_type("ActionProposed") if e.proposal_id == event.proposal_id),
            None,
        )
        params = proposal.parameters if proposal is not None else {}
        self.bus.publish(
            ReturnRejected(
                customer_id=params.get("customer_id", ""),
                order_id=params.get("order_id"),
                sku=params.get("sku", ""),
                reason=event.reason,
                decided_by=event.decided_by,
                source=self.name,
            )
        )

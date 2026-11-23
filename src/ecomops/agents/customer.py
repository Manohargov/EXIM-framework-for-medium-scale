"""Customer Requirements Agent.

Responsibility: turn unstructured customer input into a structured, catalogue-
matched intent that the rest of the system can act on — and refuse to guess
when the intent is ambiguous (that becomes a clarification, not a wrong order).

Downstream effects:
* every matched purchase line emits a ``DemandSignal`` for the clearance agent;
* an unresolvable purchase line emits ``IntentNeedsClarification`` instead of
  silently failing — the escalation path is part of the design, not an error.
"""

from __future__ import annotations

from ..events import (
    CustomerIntent,
    CustomerIntentCaptured,
    CustomerRequestReceived,
    DemandSignal,
    Event,
    IntentNeedsClarification,
    LineItem,
)
from ..inventory import Inventory
from .base import Agent


class CustomerRequirementsAgent(Agent):
    name = "customer-requirements"

    def __init__(self, bus, llm, inventory: Inventory, *, match_threshold: float = 0.25) -> None:
        super().__init__(bus, llm)
        self.inventory = inventory
        self.match_threshold = match_threshold

    def attach(self) -> None:
        self.bus.subscribe(self.name, self.handle, "CustomerRequestReceived")
        self.bus.subscribe(self.name, self.handle, "ActionApproved", "ActionRejected")

    # ---------------------------------------------------------------- react

    async def react(self, event: Event) -> None:
        assert isinstance(event, CustomerRequestReceived)
        payload = {
            "request_text": event.request_text,
            "customer_id": event.customer_id,
            "catalogue": self.inventory.catalogue_names(),
        }
        result = await self.ask(
            "capture_intent",
            "Extract the customer's intent as JSON with keys: intent_type, items "
            "([{query, quantity}]), destination, urgency, constraints, summary.",
            payload,
            schema=CustomerIntent.model_json_schema(),
        )
        intent = CustomerIntent.model_validate(result.data)

        # Resolve each line item against the catalogue (semantic match).
        resolved: list[LineItem] = []
        unresolved: list[str] = []
        for item in intent.items:
            product, _score = self.inventory.match(item.query, threshold=self.match_threshold)
            if product is None:
                unresolved.append(item.query)
                continue
            item.sku = product.sku
            item.product_name = product.name
            resolved.append(item)
        intent.items = resolved
        matched = bool(resolved) and not unresolved

        self.bus.publish(
            CustomerIntentCaptured(
                customer_id=event.customer_id,
                intent=intent,
                matched=matched,
                llm_backend=result.backend,
                request_text=event.request_text,
                source=self.name,
            )
        )

        purchase_like = intent.intent_type.value in ("purchase", "bulk_order")
        return_like = intent.intent_type.value in ("return", "complaint")
        if (purchase_like or return_like) and unresolved:
            self.bus.publish(
                IntentNeedsClarification(
                    customer_id=event.customer_id,
                    question=(
                        "We could not match these items to our catalogue: "
                        + ", ".join(unresolved)
                        + ". Could you rephrase or give a product name?"
                    ),
                    unresolved_queries=unresolved,
                    source=self.name,
                )
            )

        # Emit demand for every resolved purchase line — the bridge to clearance.
        if purchase_like:
            for item in resolved:
                assert item.sku is not None and item.product_name is not None
                self.bus.publish(
                    DemandSignal(
                        customer_id=event.customer_id,
                        sku=item.sku,
                        product_name=item.product_name,
                        quantity=item.quantity,
                        urgency=intent.urgency,
                        intent_type=intent.intent_type,
                        destination=intent.destination,
                        source=self.name,
                    )
                )

    # This agent has no side-effecting actions of its own.

    async def act(self, proposal_id: str, action: str, parameters) -> None:  # pragma: no cover
        return None

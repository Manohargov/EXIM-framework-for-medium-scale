"""Transit Management Agent.

Responsibility: once stock is reserved, plan the shipment; when delivery goes
wrong, propose a resolution. Both are LLM-assisted and both are gated:

* ``dispatch_shipment``  — the route plan is executed only after the gate
  approves the cost (autonomous under the configured limit);
* ``resolve_exception``  — reroutes under the cost limit run autonomously;
  replacements and refunds always require a human decision.

The agent keeps a shipment table so an exception can be resolved against the
original route plan (cost, carrier, ETA) rather than in isolation.
"""

from __future__ import annotations

from ..events import (
    DeliveryExceptionReported,
    DeliveryExceptionResolved,
    DemandSignal,
    Event,
    ExceptionResolution,
    ExceptionResolutionProposed,
    RoutePlan,
    ShipmentRouted,
    StockReserved,
)
from ..inventory import Inventory
from .base import Agent


class TransitManagementAgent(Agent):
    name = "transit-management"

    def __init__(self, bus, llm, inventory: Inventory) -> None:
        super().__init__(bus, llm)
        self.inventory = inventory
        self.shipments: dict[str, dict] = {}          # shipment_id -> context
        self._destinations: dict[str, str] = {}       # customer_id -> destination
        self._urgency: dict[str, str] = {}            # customer_id -> urgency

    def attach(self) -> None:
        self.bus.subscribe(
            self.name, self.handle, "StockReserved", "DemandSignal",
            "DeliveryExceptionReported", "ActionApproved", "ActionRejected",
        )

    # Routing context comes from the demand side (urgency/destination).

    async def react(self, event: Event) -> None:
        if isinstance(event, DemandSignal):
            if event.destination:
                self._destinations.setdefault(event.customer_id, event.destination)
            self._urgency[event.customer_id] = event.urgency.value
        elif isinstance(event, StockReserved):
            await self._on_reserved(event)
        elif isinstance(event, DeliveryExceptionReported):
            await self._on_exception(event)

    # ------------------------------------------------------------ routing

    async def _on_reserved(self, event: StockReserved) -> None:
        product = self.inventory.products[event.sku]
        payload = {
            "sku": event.sku,
            "product_name": product.name,
            "quantity": event.quantity,
            "destination": self._destinations.get(event.customer_id, "domestic"),
            "urgency": self._urgency.get(event.customer_id, "normal"),
            "order_value": round(event.quantity * product.unit_price, 2),
        }
        result = await self.ask(
            "plan_route",
            "Choose a carrier and service level as JSON: {carrier, service_level, "
            "eta_days, cost, risk, rationale}. Balance speed against cost and the "
            "urgency the customer asked for.",
            payload,
            schema=RoutePlan.model_json_schema(),
        )
        plan = RoutePlan.model_validate(result.data)

        self.propose(
            "dispatch_shipment",
            {
                "order_id": event.order_id,
                "sku": event.sku,
                "quantity": event.quantity,
                "customer_id": event.customer_id,
                "destination": payload["destination"],
                "plan": plan.model_dump(),
                "llm_backend": result.backend,
            },
            reason=f"{plan.carrier}/{plan.service_level}, ETA {plan.eta_days}d, ${plan.cost:.2f} — {plan.rationale}",
            estimated_value=plan.cost,
        )

    # ---------------------------------------------------------- exceptions

    async def _on_exception(self, event: DeliveryExceptionReported) -> None:
        context = self.shipments.get(event.shipment_id, {})
        product = self.inventory.products.get(context.get("sku", ""), None)
        payload = {
            "shipment_id": event.shipment_id,
            "exception": event.exception.value,
            "detail": event.detail,
            "plan": context.get("plan", {}),
            "order_value": round(context.get("quantity", 0) * (product.unit_price if product else 0.0), 2),
        }
        result = await self.ask(
            "resolve_exception",
            "A delivery exception occurred. Propose a resolution as JSON: {action "
            "(reroute|replace|refund|delay_accepted), carrier, eta_days, cost, rationale}.",
            payload,
            schema=ExceptionResolution.model_json_schema(),
        )
        resolution = ExceptionResolution.model_validate(result.data)

        self.bus.publish(
            ExceptionResolutionProposed(
                shipment_id=event.shipment_id,
                resolution=resolution,
                llm_backend=result.backend,
                source=self.name,
            )
        )
        self.propose(
            "resolve_exception",
            {
                "shipment_id": event.shipment_id,
                "resolution_action": resolution.action.value,
                "cost": resolution.cost,
                "resolution": resolution.model_dump(),
                "llm_backend": result.backend,
            },
            reason=resolution.rationale,
            estimated_value=resolution.cost,
        )

    # ------------------------------------------------------------------ act

    async def act(self, proposal_id: str, action: str, parameters) -> None:
        if action == "dispatch_shipment":
            plan = RoutePlan.model_validate(parameters["plan"])
            shipment_id = f"SHP-{parameters['order_id'].split('-')[-1]}"
            self.shipments[shipment_id] = {
                "order_id": parameters["order_id"],
                "sku": parameters["sku"],
                "quantity": parameters["quantity"],
                "customer_id": parameters["customer_id"],
                "destination": parameters["destination"],
                "plan": plan.model_dump(),
            }
            self.bus.publish(
                ShipmentRouted(
                    shipment_id=shipment_id,
                    order_id=parameters["order_id"],
                    sku=parameters["sku"],
                    quantity=parameters["quantity"],
                    destination=parameters["destination"],
                    plan=plan,
                    customer_id=parameters["customer_id"],
                    order_value=round(
                        parameters["quantity"] * self.inventory.products[parameters["sku"]].unit_price, 2
                    ),
                    source=self.name,
                )
            )
        elif action == "resolve_exception":
            resolution = ExceptionResolution.model_validate(parameters["resolution"])
            shipment_id = parameters["shipment_id"]
            decided_by = parameters.get("_approved_by", "policy")
            self.bus.publish(
                DeliveryExceptionResolved(
                    shipment_id=shipment_id,
                    resolution=resolution,
                    decided_by=str(decided_by),
                    source=self.name,
                )
            )

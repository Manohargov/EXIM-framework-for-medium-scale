"""Agent base class.

An agent is: a name, a set of subscriptions, and an LLM. It reacts to events,
publishes new ones, and never talks to another agent directly — all coordination
goes through the bus, and all side effects go through the approval gate.

The base class provides the propose → approve → act handshake so every agent
follows the same discipline:

    self.propose("reserve_stock", {...}, reason=...)   # publish ActionProposed
    ...wait for ActionApproved (or ActionRejected)...
    self.act(proposal_id, parameters)                  # perform the side effect
"""

from __future__ import annotations

import logging
from typing import Any

from ..bus import EventBus
from ..events import ActionApproved, ActionProposed, ActionRejected, EscalationRaised, Event
from ..llm import LLM, LLMResult


class Agent:
    name: str = "agent"

    def __init__(self, bus: EventBus, llm: LLM) -> None:
        self.bus = bus
        self.llm = llm
        self.log = logging.getLogger(f"ecomops.{self.name}")
        self._pending: dict[str, dict[str, Any]] = {}   # proposal_id -> parameters

    # ------------------------------------------------------------- wiring

    def attach(self, *topics: str) -> None:
        """Subscribe to ``topics`` plus the approval messages for this agent."""
        self.bus.subscribe(self.name, self.handle, *topics)
        self.bus.subscribe(
            self.name,
            self.handle,
            "ActionApproved",
            "ActionRejected",
        )

    async def handle(self, event: Event) -> None:
        if isinstance(event, ActionApproved):
            if event.proposing_agent == self.name:
                await self.on_approved(event)
            return
        if isinstance(event, ActionRejected):
            if event.proposing_agent == self.name:
                await self.on_rejected(event)
            return
        await self.react(event)

    # ------------------------------------------------------- subclasses implement

    async def react(self, event: Event) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    async def act(self, proposal_id: str, action: str, parameters: dict[str, Any]) -> None:  # pragma: no cover
        raise NotImplementedError

    # ------------------------------------------------------- propose / approve

    def propose(
        self,
        action: str,
        parameters: dict[str, Any],
        *,
        reason: str,
        estimated_value: float = 0.0,
    ) -> str:
        """Publish an ActionProposed; returns the proposal id to await."""
        p = ActionProposed(
            proposal_id="",
            proposing_agent=self.name,
            action=action,
            parameters=parameters,
            reason=reason,
            estimated_value=estimated_value,
            source=self.name,
        )
        p.proposal_id = f"{self.name}:{p.event_id}"
        self._pending[p.proposal_id] = parameters
        self.bus.publish(p)
        return p.proposal_id

    async def on_approved(self, event: ActionApproved) -> None:
        params = self._pending.pop(event.proposal_id, None)
        if params is None:
            self.log.warning("approved unknown proposal %s", event.proposal_id)
            return
        # Record who authorised the action and how — surfaced on the resulting event.
        params = {**params, "_approved_by": event.decided_by, "_approval_mode": event.mode}
        await self.act(event.proposal_id, event.action, params)

    async def on_rejected(self, event: ActionRejected) -> None:
        self._pending.pop(event.proposal_id, None)
        self.log.info("proposal %s rejected: %s", event.proposal_id, event.reason)
        self.bus.publish(
            EscalationRaised(
                agent=self.name,
                topic_name=event.action,
                reason=f"proposal rejected: {event.reason}",
                source=self.name,
            )
        )

    # ------------------------------------------------------------------ LLM

    async def ask(self, task: str, instructions: str, payload: dict[str, Any], schema: dict | None = None) -> LLMResult:
        return await self.llm.complete(task=task, instructions=instructions, payload=payload, schema=schema)

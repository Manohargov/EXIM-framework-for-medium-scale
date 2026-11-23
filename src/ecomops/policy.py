"""The approval gate: policy engine + human-in-the-loop checkpoint.

Every side-effecting action an agent wants to perform is published as an
``ActionProposed`` event and routed through this gate *before* execution. The
gate decides one of three ways:

1. **autonomous** — the action is inside policy (small discount, in-stock
   reservation, cheap reroute): approved immediately, ``mode="policy"``;
2. **human approval** — the action exceeds a policy threshold: an
   ``ApprovalRequested`` event is raised and the action *waits* until an
   ``ApprovalDecision`` arrives (from an operator, a ticketing system, or a
   client's own controls);
3. **rejected** — the action is refused outright, and the proposing agent is
   told to escalate.

The thresholds live in ``PolicyConfig`` and are deliberately conservative:
nothing that moves money beyond a small discount, and nothing that ships or
destroys stock, runs without a recorded decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .bus import EventBus
from .events import (
    ActionApproved,
    ActionProposed,
    ActionRejected,
    ApprovalDecision,
    ApprovalRequested,
    EscalationRaised,
    RiskLevel,
)


@dataclass
class PolicyConfig:
    """Thresholds. A client rollout would source these from its own controls."""

    max_autonomous_discount_pct: float = 20.0   # above this, a human approves the markdown
    max_autonomous_reservation_qty: int = 10    # above this, reserving stock needs approval
    max_autonomous_action_value: float = 200.0  # dollars at stake before human review
    max_autonomous_reroute_cost: float = 20.0   # transit exception handling without review
    refund_always_human: bool = True            # refunds never run autonomously


@dataclass
class ApprovalGate:
    """Subscribes to proposals and decisions; emits approvals/rejections."""

    bus: EventBus
    config: PolicyConfig = field(default_factory=PolicyConfig)

    def attach(self) -> None:
        self.bus.subscribe("approval-gate", self.on_proposal, "ActionProposed")
        self.bus.subscribe("approval-gate", self.on_decision, "ApprovalDecision")

    # ------------------------------------------------------------- evaluate

    def assess(self, proposal: ActionProposed) -> tuple[bool, RiskLevel, str]:
        """(requires_human, risk, reason) for one proposed action."""
        cfg = self.config
        p = proposal.parameters
        action = proposal.action

        if action == "apply_discount":
            pct = float(p.get("discount_pct", 0.0))
            if pct > cfg.max_autonomous_discount_pct:
                return True, RiskLevel.MEDIUM, f"discount {pct:.0f}% exceeds autonomous limit {cfg.max_autonomous_discount_pct:.0f}%"
            if proposal.estimated_value > cfg.max_autonomous_action_value:
                return True, RiskLevel.MEDIUM, f"value ${proposal.estimated_value:.2f} exceeds ${cfg.max_autonomous_action_value:.2f}"
            return False, RiskLevel.LOW, "within discount policy"

        if action == "reserve_stock":
            qty = int(p.get("quantity", 0))
            if qty > cfg.max_autonomous_reservation_qty:
                return True, RiskLevel.MEDIUM, f"reservation of {qty} units exceeds limit {cfg.max_autonomous_reservation_qty}"
            return False, RiskLevel.LOW, "within reservation policy"

        if action == "dispatch_shipment":
            cost = float(p.get("cost", 0.0))
            if cost > cfg.max_autonomous_action_value:
                return True, RiskLevel.MEDIUM, f"shipping cost ${cost:.2f} exceeds ${cfg.max_autonomous_action_value:.2f}"
            return False, RiskLevel.LOW, "within shipping policy"

        if action == "resolve_exception":
            resolution = str(p.get("resolution_action", ""))
            cost = float(p.get("cost", 0.0))
            if resolution == "refund" and cfg.refund_always_human:
                return True, RiskLevel.HIGH, "refunds always require a human decision"
            if resolution in ("replace", "refund") or cost > cfg.max_autonomous_reroute_cost:
                return True, RiskLevel.HIGH, f"'{resolution}' (cost ${cost:.2f}) exceeds autonomous reroute limit ${cfg.max_autonomous_reroute_cost:.2f}"
            return False, RiskLevel.LOW, "reroute within policy"

        if action == "issue_refund":
            amount = float(p.get("amount", proposal.estimated_value))
            if cfg.refund_always_human:
                return True, RiskLevel.HIGH, "refunds always require a human decision"
            if amount > cfg.max_autonomous_action_value:
                return True, RiskLevel.MEDIUM, f"refund ${amount:.2f} exceeds ${cfg.max_autonomous_action_value:.2f}"
            return False, RiskLevel.LOW, "within refund policy"

        if action == "authorise_replacement":
            value = float(p.get("value", proposal.estimated_value))
            if value > cfg.max_autonomous_action_value:
                return True, RiskLevel.MEDIUM, f"replacement value ${value:.2f} exceeds ${cfg.max_autonomous_action_value:.2f}"
            return False, RiskLevel.LOW, "within replacement policy"

        # Unknown action type: refuse to guess — escalate.
        return True, RiskLevel.HIGH, f"unknown action '{action}' cannot be assessed"

    # ------------------------------------------------------------ handlers

    async def on_proposal(self, event) -> None:  # ActionProposed
        proposal: ActionProposed = event  # type: ignore[assignment]
        requires_human, risk, reason = self.assess(proposal)

        if requires_human:
            self.bus.publish(
                ApprovalRequested(
                    proposal_id=proposal.proposal_id,
                    action=proposal.action,
                    parameters=proposal.parameters,
                    reason=reason,
                    risk=risk,
                    source="approval-gate",
                )
            )
            return

        self.bus.publish(
            ActionApproved(
                proposal_id=proposal.proposal_id,
                proposing_agent=proposal.proposing_agent,
                action=proposal.action,
                parameters=proposal.parameters,
                mode="policy",
                decided_by="policy",
                source="approval-gate",
            )
        )

    async def on_decision(self, event) -> None:  # ApprovalDecision
        decision: ApprovalDecision = event  # type: ignore[assignment]
        proposal = next(
            (e for e in self.bus.of_type("ActionProposed") if e.proposal_id == decision.proposal_id),
            None,
        )
        if proposal is None:
            self.bus.publish(
                EscalationRaised(
                    agent="approval-gate",
                    topic_name="ApprovalDecision",
                    reason=f"decision for unknown proposal {decision.proposal_id}",
                    source="approval-gate",
                )
            )
            return

        if decision.granted:
            self.bus.publish(
                ActionApproved(
                    proposal_id=proposal.proposal_id,
                    proposing_agent=proposal.proposing_agent,
                    action=proposal.action,
                    parameters=proposal.parameters,
                    mode="human",
                    decided_by=decision.decided_by,
                    source="approval-gate",
                )
            )
        else:
            self.bus.publish(
                ActionRejected(
                    proposal_id=proposal.proposal_id,
                    proposing_agent=proposal.proposing_agent,
                    action=proposal.action,
                    reason=decision.comment or "human reviewer denied the action",
                    decided_by=decision.decided_by,
                    source="approval-gate",
                )
            )


def decide(bus: EventBus, proposal_id: str, *, granted: bool, decided_by: str = "operator", comment: str = "") -> ApprovalDecision:
    """Convenience: record a human decision on a pending approval."""
    d = ApprovalDecision(
        proposal_id=proposal_id,
        granted=granted,
        decided_by=decided_by,
        comment=comment,
        source=decided_by,
    )
    return bus.publish(d)  # type: ignore[return-value]

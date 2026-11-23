"""Typed domain events. Every inter-agent message in the system is one of these.

All events share envelope metadata (ids, correlation, causation, source, time).
Correlation ids tie an entire business conversation together; causation ids
record which event directly caused another, giving an auditable chain:

    CustomerRequestReceived -> CustomerIntentCaptured -> DemandSignal
        -> ClearanceStrategyProposed -> ActionProposed -> ActionApproved
        -> StockReserved -> ShipmentRouted
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from pydantic import BaseModel, Field


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def utcnow() -> datetime:
    return datetime.now(UTC)


class Event(BaseModel):
    """Base class for all domain events."""

    model_config = {"extra": "forbid"}

    # Event class name is the topic on the bus.
    topic: ClassVar[str] = "Event"

    event_id: str = Field(default_factory=new_id)
    correlation_id: str | None = None   # resolved by the bus
    causation_id: str | None = None     # resolved by the bus
    source: str = "system"
    ts: datetime = Field(default_factory=utcnow)


# --------------------------------------------------------------------------- #
# Enumerations used inside events
# --------------------------------------------------------------------------- #

class IntentType(StrEnum):
    PURCHASE = "purchase"
    DELIVERY_STATUS = "delivery_status"
    RETURN = "return"
    COMPLAINT = "complaint"
    BULK_ORDER = "bulk_order"
    OTHER = "other"


class Urgency(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class ExceptionType(StrEnum):
    CARRIER_DELAY = "carrier_delay"
    DAMAGED = "damaged"
    LOST = "lost"
    WRONG_ADDRESS = "wrong_address"
    CUSTOMS_HOLD = "customs_hold"


class ResolutionAction(StrEnum):
    REROUTE = "reroute"
    REPLACE = "replace"
    REFUND = "refund"
    DELAY_ACCEPTED = "delay_accepted"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# --------------------------------------------------------------------------- #
# Customer Requirements Agent
# --------------------------------------------------------------------------- #

class CustomerRequestReceived(Event):
    topic = "CustomerRequestReceived"

    customer_id: str
    request_text: str
    channel: str = "web"


class LineItem(BaseModel):
    query: str                      # text as the customer phrased it
    sku: str | None = None          # resolved against the catalogue
    product_name: str | None = None
    quantity: int = 1


class CustomerIntent(BaseModel):
    intent_type: IntentType
    items: list[LineItem] = []
    destination: str | None = None
    urgency: Urgency = Urgency.NORMAL
    constraints: list[str] = []
    summary: str = ""


class CustomerIntentCaptured(Event):
    topic = "CustomerIntentCaptured"

    customer_id: str
    intent: CustomerIntent
    matched: bool                  # every line item resolved to a catalogue SKU
    llm_backend: str
    request_text: str = ""         # original wording, kept for downstream agents


class IntentNeedsClarification(Event):
    topic = "IntentNeedsClarification"

    customer_id: str
    question: str
    unresolved_queries: list[str]


class DemandSignal(Event):
    topic = "DemandSignal"

    customer_id: str
    sku: str
    product_name: str
    quantity: int
    urgency: Urgency
    intent_type: IntentType
    destination: str | None = None


# --------------------------------------------------------------------------- #
# Task extraction (inbox + customer form)
# --------------------------------------------------------------------------- #


class EmailReceived(Event):
    """One message pulled from an operator's IMAP inbox."""

    topic = "EmailReceived"

    sender: str
    body: str
    recipients: list[str] = []
    subject: str = ""
    message_id: str = ""
    assignee_email: str = ""        # the mailbox owner — tasks default to them


class FormReceived(Event):
    """A customer form submission, the second source of extracted tasks."""

    topic = "FormReceived"

    text: str
    fields: dict[str, str] = {}
    form_name: str = "customer"
    submitted_by: str = ""
    assignee_email: str = ""


class ExtractedTask(BaseModel):
    """The shape both the rules and the optional LLM return."""

    title: str
    description: str = ""
    assignee_email: str | None = None
    priority: str = "normal"        # low | normal | high
    due_date: str | None = None


class TaskList(BaseModel):
    tasks: list[ExtractedTask] = []


class TaskExtracted(Event):
    """An actionable item surfaced on a user's dashboard."""

    topic = "TaskExtracted"

    task_id: str
    title: str
    description: str = ""
    assignee_email: str
    priority: str = "normal"
    due_date: str | None = None
    status: str = "open"            # open | in_progress | done
    origin: str = "email"           # email | form (Event.source stays audit-only)
    source_event_id: str | None = None
    sender: str = ""


class TaskExtractionCompleted(Event):
    """Audit summary for one inbox/form extraction pass."""

    topic = "TaskExtractionCompleted"

    origin: str
    count: int
    assignee_email: str = ""
    source_event_id: str | None = None
    llm_backend: str = ""


class TaskStatusChanged(Event):
    """Append-only status transition — the current state is folded from these."""

    topic = "TaskStatusChanged"

    task_id: str
    status: str
    previous_status: str = ""
    changed_by: str = ""


# --------------------------------------------------------------------------- #
# Stock Clearance Agent
# --------------------------------------------------------------------------- #

class StockLevelReport(Event):
    """Periodic inventory position; triggers proactive clearance planning."""

    topic = "StockLevelReport"

    overstocked: list[str]         # skus with age_days >= threshold
    total_units: int
    total_overstocked: int = 0     # before any per-pass cap (len(overstocked) may be smaller)


class ClearanceStrategy(BaseModel):
    sku: str
    discount_pct: float
    bundle_with: list[str] = []
    substitute_skus: list[str] = []
    rationale: str


class ClearanceStrategyProposed(Event):
    topic = "ClearanceStrategyProposed"

    strategy: ClearanceStrategy
    trigger: str                   # "demand" | "overstock"
    llm_backend: str


class DiscountApplied(Event):
    topic = "DiscountApplied"

    sku: str
    discount_pct: float
    previous_price: float
    new_price: float


class StockReserved(Event):
    topic = "StockReserved"

    sku: str
    quantity: int
    customer_id: str
    order_id: str


class StockReservationFailed(Event):
    topic = "StockReservationFailed"

    sku: str
    requested: int
    available: int
    customer_id: str


# --------------------------------------------------------------------------- #
# Transit Management Agent
# --------------------------------------------------------------------------- #

class RoutePlan(BaseModel):
    carrier: str
    service_level: str
    eta_days: float
    cost: float
    risk: RiskLevel
    rationale: str


class ShipmentRouted(Event):
    topic = "ShipmentRouted"

    shipment_id: str
    order_id: str
    sku: str
    quantity: int
    destination: str
    plan: RoutePlan
    customer_id: str = ""          # lets downstream agents attach returns to a buyer
    order_value: float = 0.0       # price when dispatched — refunds use this, not today's price


class DeliveryExceptionReported(Event):
    topic = "DeliveryExceptionReported"

    shipment_id: str
    exception: ExceptionType
    detail: str


class ExceptionResolution(BaseModel):
    action: ResolutionAction
    carrier: str | None = None
    eta_days: float | None = None
    cost: float = 0.0
    rationale: str


class ExceptionResolutionProposed(Event):
    topic = "ExceptionResolutionProposed"

    shipment_id: str
    resolution: ExceptionResolution
    llm_backend: str


class DeliveryExceptionResolved(Event):
    topic = "DeliveryExceptionResolved"

    shipment_id: str
    resolution: ExceptionResolution
    decided_by: str


# --------------------------------------------------------------------------- #
# Returns & Refunds Agent
# --------------------------------------------------------------------------- #

class ReturnAction(StrEnum):
    REFUND = "refund"
    REPLACEMENT = "replacement"
    REJECT = "reject"


class ReturnRequested(Event):
    """A customer wants to return or be compensated for a delivered item."""

    topic = "ReturnRequested"

    customer_id: str
    sku: str
    product_name: str
    quantity: int
    order_id: str | None = None
    reason: str = ""


class ReturnAssessment(BaseModel):
    action: ReturnAction
    eligible: bool
    refund_amount: float = 0.0
    restocking_fee: float = 0.0
    rationale: str


class ReturnAssessed(Event):
    topic = "ReturnAssessed"

    customer_id: str
    sku: str
    quantity: int
    order_id: str | None = None
    assessment: ReturnAssessment
    llm_backend: str


class ReturnNeedsOrder(Event):
    """A return cannot be processed without a matching delivered order."""

    topic = "ReturnNeedsOrder"

    customer_id: str
    sku: str | None = None
    question: str


class RefundIssued(Event):
    topic = "RefundIssued"

    customer_id: str
    order_id: str | None = None
    sku: str
    quantity: int
    amount: float
    restocked: bool
    decided_by: str


class ReplacementAuthorised(Event):
    topic = "ReplacementAuthorised"

    customer_id: str
    order_id: str | None = None
    sku: str
    quantity: int
    decided_by: str


class ReturnRejected(Event):
    topic = "ReturnRejected"

    customer_id: str
    order_id: str | None = None
    sku: str
    reason: str
    decided_by: str


# --------------------------------------------------------------------------- #
# Approval gate (policy + human)
# --------------------------------------------------------------------------- #

class ActionProposed(Event):
    """An agent wants to execute a side-effecting action. Routed through the gate."""

    topic = "ActionProposed"

    proposal_id: str
    proposing_agent: str
    action: str                    # reserve_stock | apply_discount | dispatch_shipment
                                   # | resolve_exception | issue_refund | authorise_replacement
    parameters: dict[str, Any]
    reason: str
    estimated_value: float = 0.0   # monetary value the action puts at stake


class ApprovalRequested(Event):
    topic = "ApprovalRequested"

    proposal_id: str
    action: str
    parameters: dict[str, Any]
    reason: str
    risk: RiskLevel


class ApprovalDecision(Event):
    topic = "ApprovalDecision"

    proposal_id: str
    granted: bool
    decided_by: str
    comment: str = ""


class ActionApproved(Event):
    topic = "ActionApproved"

    proposal_id: str
    proposing_agent: str
    action: str
    parameters: dict[str, Any]
    mode: str                      # "policy" (autonomous) | "human"
    decided_by: str = "policy"


class ActionRejected(Event):
    topic = "ActionRejected"

    proposal_id: str
    proposing_agent: str
    action: str
    reason: str
    decided_by: str


class EscalationRaised(Event):
    topic = "EscalationRaised"

    agent: str
    topic_name: str
    reason: str


class TransitNoticeSent(Event):
    """A shipment notice was sent to the buyer waiting for that order."""

    topic = "TransitNoticeSent"

    shipment_id: str
    order_id: str = ""
    customer_id: str
    destination: str = ""
    carrier: str = ""
    service_level: str = ""
    eta_days: float = 0.0
    items: list[str] = Field(default_factory=list)
    caption: str = ""
    channel: str = "email"
    sent_by: str = ""


class PromotionPublished(Event):
    """A finished advertisement was pushed to customers."""

    topic = "PromotionPublished"

    company: str
    product_name: str
    sku: str = ""
    old_price: float = 0.0
    new_price: float = 0.0
    currency: str = "$"
    items: list[str] = Field(default_factory=list)
    caption: str = ""
    channel: str = "email"
    recipients: list[str] = Field(default_factory=list)
    published_by: str = ""


# --------------------------------------------------------------------------- #
# Registry — topic name -> event class
# --------------------------------------------------------------------------- #

def event_classes() -> dict[str, type[Event]]:
    """Every concrete event class, keyed by bus topic.

    Used by the journal and the HTTP API to rebuild typed events from JSON.
    Walked recursively so a new event only needs to subclass ``Event`` to be
    picked up everywhere.
    """
    found: dict[str, type[Event]] = {}

    def walk(cls: type[Event]) -> None:
        for sub in cls.__subclasses__():
            found[sub.topic] = sub
            walk(sub)

    walk(Event)
    return found


def event_for(topic: str, body: dict[str, Any]) -> Event:
    """Rebuild a typed event from a journal/API payload."""
    cls = event_classes().get(topic)
    if cls is None:
        raise KeyError(f"unknown event topic {topic!r}")
    return cls.model_validate(body)

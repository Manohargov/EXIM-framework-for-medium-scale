"""System assembly: bus + approval gate + four agents.

``EcomopsSystem`` is the composition root. It knows nothing about the business
logic — it wires the pieces, exposes the entry points an outer world (CLI, web
app, message broker bridge) would use, and drains the bus.

Entry points mirror what a real deployment would receive:

    receive_customer_request(...)   <- web form, email, chat
    report_stock_levels(...)        <- warehouse WMS
    report_delivery_exception(...)  <- carrier webhook
    decide(proposal_id, granted)    <- operator / client approval portal
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agents.clearance import StockClearanceAgent
from .agents.customer import CustomerRequirementsAgent
from .agents.returns import ReturnPolicy, ReturnsAgent
from .agents.tasks import TaskExtractionAgent
from .agents.transit import TransitManagementAgent
from .bus import EventBus
from .catalogue import GroceryLoadReport, load_grocery_catalogue, merge_catalogues
from .events import (
    CustomerRequestReceived,
    DeliveryExceptionReported,
    EmailReceived,
    FormReceived,
    StockLevelReport,
)
from .inventory import CATALOGUE, Inventory, Product
from .journal import EventJournal
from .llm import LLM, Embedder, TokenOverlapEmbedder, default_embedder
from .policy import ApprovalGate, PolicyConfig, decide
from .rules import build_rule_llm


@dataclass
class EcomopsSystem:
    bus: EventBus = field(default_factory=EventBus)
    llm: LLM = field(default_factory=build_rule_llm)
    embedder: Embedder = field(default_factory=TokenOverlapEmbedder)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    return_policy: ReturnPolicy = field(default_factory=ReturnPolicy)
    journal: EventJournal | None = None
    catalogue: list[Product] | None = None          # None -> the built-in demo SKUs
    grocery_report: GroceryLoadReport | None = None  # set when a CSV pack was merged

    def __post_init__(self) -> None:
        self.inventory = Inventory.with_catalogue(self.embedder, self.catalogue)
        self.gate = ApprovalGate(self.bus, self.policy)
        self.customer = CustomerRequirementsAgent(self.bus, self.llm, self.inventory)
        self.clearance = StockClearanceAgent(self.bus, self.llm, self.inventory)
        self.transit = TransitManagementAgent(self.bus, self.llm, self.inventory)
        self.returns = ReturnsAgent(
            self.bus, self.llm, self.inventory, policy=self.return_policy
        )
        self.tasks = TaskExtractionAgent(self.bus, self.llm)
        for component in (
            self.gate, self.customer, self.clearance, self.transit, self.returns, self.tasks
        ):
            component.attach()
        if self.journal is not None:
            self.journal.attach(self.bus)

    # --------------------------------------------------------------- journal

    def attach_journal(self, path: str | Path, *, truncate: bool = False) -> EventJournal:
        """Persist every event to ``path`` (JSONL) from now on."""
        self.journal = EventJournal(path, truncate=truncate)
        self.journal.attach(self.bus)
        return self.journal

    # ------------------------------------------------------------- entry points

    async def receive_customer_request(
        self, customer_id: str, request_text: str, *, channel: str = "web"
    ) -> None:
        self.bus.publish(
            CustomerRequestReceived(
                customer_id=customer_id, request_text=request_text, channel=channel, source="inbound"
            )
        )
        await self.bus.drain()

    async def receive_email(
        self,
        *,
        sender: str,
        body: str,
        subject: str = "",
        recipients: list[str] | None = None,
        message_id: str = "",
        assignee_email: str = "",
    ) -> None:
        """Ingest one inbox message; the task agent turns it into dashboard items.

        ``assignee_email`` is the mailbox owner. When omitted it falls back to
        the configured IMAP user, so a scan always lands on somebody's dashboard.
        """
        if not assignee_email:
            from .mail import config_from_env

            assignee_email = config_from_env().username
        self.bus.publish(
            EmailReceived(
                sender=sender,
                body=body,
                recipients=list(recipients or []),
                subject=subject,
                message_id=message_id,
                assignee_email=assignee_email,
                source="imap",
            )
        )
        await self.bus.drain()

    async def receive_form(
        self,
        *,
        text: str = "",
        fields: dict[str, str] | None = None,
        form_name: str = "customer",
        submitted_by: str = "",
        assignee_email: str = "",
    ) -> None:
        """Ingest a customer form — the second source of extracted tasks."""
        self.bus.publish(
            FormReceived(
                text=text,
                fields=dict(fields or {}),
                form_name=form_name,
                submitted_by=submitted_by,
                assignee_email=assignee_email,
                source="web",
            )
        )
        await self.bus.drain()

    async def report_stock_levels(
        self, *, min_age_days: int = 90, min_stock: int = 100, max_items: int = 25
    ) -> None:
        """Report the most valuable overstocked SKUs.

        A merged grocery catalogue can have hundreds of ageing lines; a clearance
        pass is bounded to the costliest ``max_items`` so the approval queue stays
        operable. The full count travels on the event as ``total_overstocked``.
        """
        overstocked = self.inventory.overstocked(min_age_days=min_age_days, min_stock=min_stock)
        overstocked.sort(key=lambda p: p.inventory_value, reverse=True)
        selected = overstocked[:max_items] if max_items >= 0 else overstocked
        self.bus.publish(
            StockLevelReport(
                overstocked=[p.sku for p in selected],
                total_units=sum(p.stock for p in self.inventory.products.values()),
                total_overstocked=len(overstocked),
                source="wms",
            )
        )
        await self.bus.drain()

    async def report_delivery_exception(
        self, shipment_id: str, exception: str, detail: str = ""
    ) -> None:
        from .events import ExceptionType

        self.bus.publish(
            DeliveryExceptionReported(
                shipment_id=shipment_id,
                exception=ExceptionType(exception),
                detail=detail,
                source="carrier-webhook",
            )
        )
        await self.bus.drain()

    async def decide(self, proposal_id: str, *, granted: bool, decided_by: str = "operator", comment: str = "") -> None:
        decide(self.bus, proposal_id, granted=granted, decided_by=decided_by, comment=comment)
        await self.bus.drain()

    # -------------------------------------------------------------- shortcuts

    def metrics(self) -> dict:
        """Counters and gauges derived from the event history."""
        from .metrics import snapshot

        return snapshot(self.bus, self.inventory)

    @property
    def pending_approvals(self):
        return self.bus.pending_approvals

    def shipments(self) -> dict[str, dict]:
        return self.transit.shipments

    # ------------------------------------------------------------------ tasks

    def my_tasks(self, email: str, *, all_users: bool = False):
        """Current tasks for one operator, folded from the event history."""
        from .tasks import my_tasks

        return my_tasks(self.bus, email, all_users=all_users)

    def update_task_status(self, task_id: str, status: str, *, changed_by: str):
        """Append a status transition for a task on this system's bus."""
        from .tasks import change_status

        return change_status(self.bus, task_id, status, changed_by=changed_by)

    # ------------------------------------------------------------- documents

    def open_document(
        self,
        path: str | Path,
        user: Any,
        *,
        workdir: str | Path | None = None,
        filename: str | None = None,
    ):
        """Read a PDF or image, resolve it against this system, return a session.

        Lazy import: document intake needs Pillow, which the core agents do not.
        """
        from .documents.pipeline import open_document

        return open_document(self, user, path, workdir=workdir, filename=filename)



def build_system(
    *,
    use_mlx: bool = False,
    model: str | None = None,
    embedder: Embedder | None = None,
    journal_path: str | Path | None = None,
    grocery: str | Path | None = None,
    grocery_limit: int | None = None,
) -> EcomopsSystem:
    """Compose a system. ``use_mlx`` enables the real MLX model with rule fallback.

    ``grocery`` merges a grocery CSV (e.g. ``data/grocery_catalogue.csv``) next to
    the ten demo SKUs — useful for UI/API testing with a realistic catalogue.
    """
    emb = embedder or default_embedder()
    if use_mlx:
        from .llm import FallbackLLM, MlxLLM

        primary = MlxLLM(model=model) if model else MlxLLM()
        llm: LLM = FallbackLLM(primary=primary, secondary=build_rule_llm())
    else:
        llm = build_rule_llm()

    catalogue: list[Product] | None = None
    report: GroceryLoadReport | None = None
    if grocery:
        products, report = load_grocery_catalogue(grocery, limit=grocery_limit)
        catalogue = merge_catalogues(CATALOGUE, products)

    system = EcomopsSystem(
        llm=llm, embedder=emb, catalogue=catalogue, grocery_report=report
    )
    if journal_path is not None:
        system.attach_journal(journal_path)
    return system

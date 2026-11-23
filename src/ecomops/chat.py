"""Sidebar assistant: role-aware answers about the running system.

Two backends, tried in order:

1. **Deterministic intents** — exact, cheap answers for the questions an
   operator actually asks ("what is pending?", "stock of paper towels?",
   "refund total?"). These also enforce the access matrix: finance figures are
   only included for roles with ``view_finance``.
2. **LangChain** — when ``langchain-core`` + ``langchain-openai`` are installed
   and an OpenAI-compatible endpoint answers (Lemonade Server by default), the
   question goes to the local model with a compact JSON snapshot of the system
   as context. Any failure falls back to the deterministic summary, so the UI
   never breaks because a model is missing.

Install the optional backend with ``pip install 'ecomops[chat]'``; point it at a
different endpoint with ``ECOMOPS_CHAT_BASE_URL`` / ``ECOMOPS_CHAT_MODEL``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .auth import FINANCE_ACTIONS, Permission, User
from .chatmodel import get_connector
from .finance import finance_snapshot, finance_summary_line

SYSTEM_PROMPT = (
    "You are MSM Assistant, the chat helper inside an event-driven e-commerce "
    "operations console. Answer in at most 3 short sentences (or 4 short bullets) "
    "using ONLY the JSON snapshot. Never invent numbers. The user's role and "
    "permissions are in the snapshot; if a question needs finance data the role "
    "cannot see, say so and suggest asking an accountant or the MD. Never mention "
    "product ids, order ids or numbers that are not in the snapshot; if the "
    "snapshot is empty, say there is no activity yet. When the user asks about a "
    "document, PDF, scan, photo, invoice or attachment, explain that they can "
    "attach it with the + control in the composer: you read it locally, fill it "
    "from the catalogue and event store, and ask only for what is still missing. "
    "Never claim to have read a document that is not in the snapshot."
)

_GREETING = re.compile(r"^(hi|hello|hey|good (morning|afternoon|evening))\b", re.I)
_PRICE_OF = re.compile(r"(?:price|cost|stock|how many)\s+(?:of|for|do we have)?\s*(.+)", re.I)



@dataclass
class OpsChat:
    system: Any
    user: User
    history: list[dict[str, str]] = field(default_factory=list)
    document: Any = None  # DocumentSession when a document is open in the assistant

    # ------------------------------------------------------------ snapshot

    def snapshot(self) -> dict[str, Any]:
        system = self.system
        bus = system.bus
        requests = bus.of_type("CustomerRequestReceived")
        data: dict[str, Any] = {
            "user": {"email": self.user.email, "role": self.user.role.value},
            "permissions": sorted(p.value for p in Permission if self.user.can(p)),
            "events": len(bus.history),
            "customer_requests": len(requests),
            "customers": len({e.customer_id for e in requests}),
            "requests_needing_clarification": len(bus.of_type("IntentNeedsClarification")),
            "purchase_lines": len(bus.of_type("DemandSignal")),
            "recent_requests": [
                {
                    "customer": e.customer_id,
                    "text": e.request_text[:120],
                    "channel": e.channel,
                }
                for e in requests[-5:]
            ],
            "pending_approvals": [
                {"action": e.action, "risk": e.risk.value, "reason": e.reason}
                for e in system.pending_approvals[:10]
            ],
            "reservations": len(bus.of_type("StockReserved")),
            "shipments": len(bus.of_type("ShipmentRouted")),
            "exceptions": len(bus.of_type("DeliveryExceptionResolved")),
            "returns_assessed": len(bus.of_type("ReturnAssessed")),
            "discounts": len(bus.of_type("DiscountApplied")),
            "products": len(system.inventory.products),
            "units": sum(p.stock for p in system.inventory.products.values()),
        }
        if self.user.can(Permission.VIEW_FINANCE):
            data["finance"] = finance_snapshot(system)
        if self.document is not None:
            job = self.document.job
            data["document"] = {
                "filename": job.filename,
                "type": job.doc_type.value,
                "pages": job.pages,
                "status": job.status,
                "fields_known": sum(1 for f in job.fields if not f.needs_input),
                "fields_total": len(job.fields),
                "still_needed": [f.label for f in job.blocking],
                "optional": [f.label for f in job.missing if not f.required],
            }
        return data

    # -------------------------------------------------------------- answer

    def answer(self, question: str) -> str:
        question = (question or "").strip()
        if not question:
            return "Ask me about stock, shipments, approvals, returns or finance."
        ruled = self._rule_answer(question)
        if ruled is not None:
            return ruled
        generated = self._langchain_answer(question)
        if generated:
            return generated
        return self._summary()

    # --------------------------------------------------------------- rules

    def _rule_answer(self, question: str) -> str | None:
        q = question.lower().strip()
        system = self.system
        bus = system.bus

        if _GREETING.match(q):
            return (
                f"Hi {self.user.name.split()[0]} — you are signed in as "
                f"{self.user.label}. Ask about stock, pending approvals, shipments, "
                f"returns or (if your role allows) finance."
            )
        if any(word in q for word in ("who am i", "my role", "my access", "permissions")):
            allowed = ", ".join(sorted(p.value for p in Permission if self.user.can(p)))
            return f"{self.user.name} · {self.user.label} ({self.user.email}). Allowed: {allowed}."
        if "help" in q and len(q) < 40:
            return (
                "I can report customer requests, stock levels, pending approvals, "
                "shipments, returns, the event count and finance totals. Finance and "
                "audit answers are restricted by role. Attach a PDF or image with the "
                "document button in the composer and I will read it."
            )

        if self.document is not None:
            reply = self._document_answer(q)
            if reply is not None:
                return reply

        document_phrases = ("document", "attach", "upload", " pdf", "scan")
        if any(phrase in q for phrase in document_phrases) or q.startswith(("can you read", "read this")):
            return (
                "Attach a PDF, PNG or JPEG in the composer below (the attachment control "
                "next to the message box). I read it locally, fill every field I can "
                "answer from the catalogue and event store, and ask you only for the rest."
            )

        request_phrases = (
            "customer request",
            "customer requests",
            "how many request",
            "requests came",
            "request came",
            "how many order",
            "orders came",
            "how many customer",
            "demand signal",
            "purchase line",
        )
        if any(phrase in q for phrase in request_phrases):
            return self._request_answer()

        if any(word in q for word in ("approval", "pending", "queue")):
            if not self.user.can_any(Permission.APPROVE_OPERATIONS, Permission.APPROVE_FINANCE):
                return "Your role cannot view the approval queue. A supervisor, accountant or the MD can."
            pending = [
                e for e in system.pending_approvals
                if self.user.can(
                    Permission.APPROVE_FINANCE
                    if e.action in FINANCE_ACTIONS
                    else Permission.APPROVE_OPERATIONS
                )
            ]
            if not pending:
                return "Nothing pending in your approval scope."
            lines = [f"{len(pending)} pending:"]
            for event in pending[:6]:
                lines.append(f"• {event.action} ({event.risk.value}) — {event.reason[:70]}")
            return "\n".join(lines)

        if any(word in q for word in ("refund", "revenue", "finance", "referral", "commission", "sales", "margin")):
            if not self.user.can(Permission.VIEW_FINANCE):
                return (
                    "Finance data is restricted to the accountant and the MD. "
                    "Your role can still act on orders, stock and logistics."
                )
            return "Finance · " + finance_summary_line(finance_snapshot(system))

        if any(word in q for word in ("audit", "event trace", "replay")) and not self.user.can(Permission.VIEW_AUDIT):
            return "The audit trail is restricted to supervisors and the MD."

        if any(word in q for word in ("product", "catalogue", "catalog", "inventory", "stock", "sku")):
            match = _PRICE_OF.search(question)
            if match:
                query = match.group(1).strip(" ?.!")
                product, _score = system.inventory.match(query)
                if product is not None:
                    return (
                        f"{product.name} ({product.sku}) · ${product.unit_price:.2f} · "
                        f"{product.stock} units on hand · {product.category}."
                    )
                return f"I could not match {query!r} to the catalogue."
            products = list(system.inventory.products.values())
            units = sum(p.stock for p in products)
            lines = [f"{len(products)} products · {units:,} units on hand."]
            over = system.inventory.overstocked()
            if over:
                lines.append(f"{len(over)} lines are overstocked or ageing.")
            if self.user.can(Permission.VIEW_FINANCE):
                lines.append(f"Inventory value ${sum(p.inventory_value for p in products):,.2f}.")
            return " ".join(lines)

        if any(word in q for word in ("shipment", "transit", "carrier", "delivery", "eta")):
            shipments = system.shipments()
            if not shipments:
                return "No shipments are routed yet — approve a dispatch proposal first."
            first = next(iter(shipments.items()))
            sid, ctx = first
            return (
                f"{len(shipments)} shipment(s) routed. Latest {sid}: {ctx['quantity']} x "
                f"{ctx['sku']} to {ctx['destination']} via {ctx['plan']['carrier']}, "
                f"ETA {ctx['plan']['eta_days']} day(s)."
            )

        if any(word in q for word in ("return", "replacement", "damaged")):
            assessed = bus.of_type("ReturnAssessed")
            rejected = bus.of_type("ReturnRejected")
            replacements = bus.of_type("ReplacementAuthorised")
            return (
                f"{len(assessed)} return(s) assessed · {len(rejected)} rejected · "
                f"{len(replacements)} replacement(s) authorised."
            )

        if any(word in q for word in ("event", "trace", "log", "audit")):
            if not self.user.can(Permission.VIEW_AUDIT):
                return "The audit trail is restricted to supervisors and the MD."
            topics = sorted({record.topic for record in bus.history})
            return f"{len(bus.history)} events on the bus across {len(topics)} topic(s): " + ", ".join(topics[:8])
        return None

    def _document_answer(self, q: str) -> str | None:
        """Answer about the document that is open in the panel."""
        job = self.document.job
        pages = "page" if job.pages == 1 else "pages"
        head = f"**{job.filename}** · {job.doc_type.label} · {job.pages} {pages}"

        if any(word in q for word in ("missing", "need", "gap", "left", "still", "what else")):
            if job.blocking:
                detail = "; ".join(
                    f"{field.label} ({field.hint or 'needs a value'})" for field in job.blocking[:5]
                )
                return (
                    f"{head}\n\nStill needed: {detail}.\n"
                    "Reply with `key=value`, e.g. `sku=SKU-1001` — or `key=` to leave it blank."
                )
            optional = [f for f in job.missing if not f.required]
            text = f"{head}\n\nNothing blocking is missing — press **Generate document** in the panel."
            if optional:
                text += " Optional: " + ", ".join(f.label for f in optional) + " (can stay blank)."
            return text

        if any(word in q for word in ("generate", "download", "fill", "produce", "pdf")):
            if job.blocking:
                return (
                    f"{head}\n\nAnswer the {len(job.blocking)} remaining field(s) first: "
                    + ", ".join(f.label for f in job.blocking)
                    + "."
                )
            if job.output_path is not None and job.status == "filled":
                return (
                    f"{head}\n\nThe filled PDF is ready — use **Download filled PDF** "
                    "in the Document panel. Press Generate again to re-render it."
                )
            return (
                f"{head}\n\nPress **Generate document** in the Document panel under the chat; "
                "the filled PDF appears with a preview and a download button."
            )

        if any(word in q for word in ("document", "attachment", "attached", "file", "this")):
            known = sum(1 for f in job.fields if not f.needs_input)
            return (
                f"{head}\n\n{known} of {len(job.fields)} fields answered from MSM. "
                "Ask “what is missing?” or answer with `key=value`."
            )
        return None

    def _request_answer(self) -> str:
        """Answer from the event store, not from the model's guess."""
        bus = self.system.bus
        requests = bus.of_type("CustomerRequestReceived")
        if not requests:
            return "No customer requests have been received yet."
        customers = len({e.customer_id for e in requests})
        captured = bus.of_type("CustomerIntentCaptured")
        clarification = bus.of_type("IntentNeedsClarification")
        demand = bus.of_type("DemandSignal")
        lines = [
            f"{len(requests)} customer request(s) from {customers} customer(s) · "
            f"{len(captured)} intent(s) captured · {len(demand)} purchase line(s)."
        ]
        if clarification:
            lines.append(f"{len(clarification)} request(s) need clarification.")
        latest = requests[-1]
        text = latest.request_text.strip()
        if text:
            lines.append(f"Latest: {latest.customer_id} — {text[:90]} ({latest.channel}).")
        return "\n".join(lines)

    # ----------------------------------------------------------- LangChain

    def _langchain_answer(self, question: str) -> str | None:
        """Ask the configured local model; ``None`` keeps the rule fallback."""
        snapshot = json.dumps(self.snapshot(), default=str)
        text = get_connector().invoke(SYSTEM_PROMPT, question, snapshot)
        # Small models sometimes invent figures: reject any number that is not
        # present in the snapshot and fall back to the deterministic summary.
        if text and not _numbers_grounded(text, snapshot):
            return None
        return text

    # ------------------------------------------------------------ fallback

    def _summary(self) -> str:
        system = self.system
        parts = [
            f"{len(system.bus.history)} events",
            f"{len(system.bus.of_type('CustomerRequestReceived'))} customer request(s)",
            f"{len(system.pending_approvals)} pending approval(s)",
            f"{len(system.bus.of_type('ShipmentRouted'))} shipment(s) routed",
            f"{len(system.bus.of_type('ReturnAssessed'))} return(s) assessed",
        ]
        if self.user.can(Permission.VIEW_FINANCE):
            parts.append(finance_summary_line(finance_snapshot(system)))
        return "Current state: " + " · ".join(parts) + "."


_NUMBER_RE = re.compile(r"\d[\d,\.]*")


def _number_values(text: str) -> set[float]:
    values: set[float] = set()
    for token in _NUMBER_RE.findall(text):
        try:
            values.add(round(float(token.replace(",", "").rstrip(".")), 2))
        except ValueError:
            continue
    return values


def _numbers_grounded(text: str, snapshot: str) -> bool:
    """True when every number in ``text`` also appears in the snapshot."""
    return _number_values(text) <= _number_values(snapshot)


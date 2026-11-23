"""Deterministic handlers for each LLM task — the offline fallback "model".

Each handler mirrors what the real prompt asks the MLX model to produce: the same
keys, the same shapes. Agents validate both paths through the same pydantic
schemas, so a rule-based run and an MLX run exercise identical downstream logic.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .llm import RuleBasedLLM

_WORD_QTY = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "pair": 1, "dozen": 12, "couple": 2,
}

_BULK_WORDS = {"pallet", "wholesale", "bulk", "container", "truckload", "cases", "cartons"}

# Words that sit between a quantity and the product it refers to.
_FILLER = {"of", "the", "a", "an", "for", "with", "and", "x"}
_UNIT_WORDS = {
    "pair", "pairs", "unit", "units", "piece", "pieces", "pcs", "set", "sets",
    "box", "boxes", "jar", "jars", "bottle", "bottles", "bag", "bags",
    "can", "cans", "pack", "packs", "case", "cases", "carton", "cartons",
    "tub", "tubs", "pouch", "pouches", "loaf", "loaves", "bunch", "bunches",
}


# Words that end a product phrase in free text: destination and urgency markers.
# ("2 jars of peanut butter to Berlin" -> "peanut butter")
_STOP_WORDS = {
    "to", "in", "near", "from", "at", "please", "asap", "urgent",
    "today", "immediately", "now", "deliver", "delivered", "ship", "shipped",
}


def _extract_items(text: str, catalogue_names: list[str]) -> list[dict[str, Any]]:
    """Pull (product-ish phrase, quantity) pairs out of free text.

    Matches known catalogue names by substring — consuming the quantity phrase
    that precedes them — then falls back to a "<quantity> <noun phrase>" pattern
    for products outside the catalogue.
    """
    lower = text.lower()
    items: list[dict[str, Any]] = []
    consumed: list[tuple[int, int]] = []

    for name in sorted(catalogue_names, key=len, reverse=True):
        idx = lower.find(name.lower())
        if idx == -1:
            continue
        start = max(0, idx - 40)
        qty, qty_at = _qty_before(lower[start:idx], start)
        items.append({"query": name, "quantity": qty})
        consumed.append((qty_at, idx + len(name)))

    # "<number/word> <noun phrase>" for anything the catalogue loop did not claim.
    for m in re.finditer(
        r"\b(\d+|" + "|".join(_WORD_QTY) + r")\s+([a-z][a-z\- ]{2,30})(?=[\s.,!?]|$)", lower
    ):
        if any(m.start() < e and m.end() > s for s, e in consumed):
            continue
        qty_word = m.group(1)
        qty = int(qty_word) if qty_word.isdigit() else _WORD_QTY[qty_word]
        words = m.group(2).split()
        # A greedy capture runs into the destination/urgency tail; cut it there.
        stop = next((i for i, word in enumerate(words) if word in _STOP_WORDS), len(words))
        words = words[:stop]
        # "2 jars of peanut butter" / "a bottle of olive oil" -> drop the
        # container phrase: whatever precedes "of" names the package, not the product.
        if "of" in words[:3]:
            words = words[words.index("of") + 1:]
        # "2 jars peanut butter" -> "peanut butter" (container without "of").
        while words and words[0] in _UNIT_WORDS | _FILLER:
            words.pop(0)
        phrase = " ".join(words).strip()
        if qty >= 1 and 1 <= len(words) <= 5:
            items.append({"query": phrase, "quantity": qty})
            consumed.append((m.start(), m.end()))
    return items


def _qty_before(window: str, base: int) -> tuple[int, int]:
    """(quantity, absolute index of the quantity token) found before a product name."""
    words = [(m.group(0).strip(".,!?"), base + m.start()) for m in re.finditer(r"\S+", window)]
    for word, pos in reversed(words):
        if word in _FILLER or word in _UNIT_WORDS:
            continue
        if word.isdigit():
            return max(1, int(word)), pos
        if word in _WORD_QTY:
            return _WORD_QTY[word], pos
        break  # first content word is not a quantity
    return 1, base


def capture_intent(payload: dict[str, Any]) -> dict[str, Any]:
    text: str = payload["request_text"]
    lower = text.lower()
    catalogue_names: list[str] = payload.get("catalogue", [])

    items = _extract_items(text, catalogue_names)

    if any(w in lower for w in _BULK_WORDS):
        intent_type = "bulk_order"
    elif any(
        w in lower
        for w in ("refund", "return", "broken", "damaged", "wrong item", "replace", "exchange", "faulty")
    ):
        intent_type = "return"
    elif any(w in lower for w in ("where is", "tracking", "delivery", "arrive", "shipment", "eta")):
        intent_type = "delivery_status"
    elif any(w in lower for w in ("complaint", "unhappy", "terrible", "never received")):
        intent_type = "complaint"
    elif items:
        intent_type = "purchase"
    else:
        intent_type = "other"

    urgency = "high" if any(w in lower for w in ("urgent", "asap", "today", "immediately", "express")) else "normal"
    if intent_type == "bulk_order":
        urgency = "high"

    dest = None
    m = re.search(r"\b(?:to|in|near)\s+([A-Z][A-Za-z]+(?:\s[A-Z][A-Za-z]+)?)", text)
    if m:
        # A trailing urgency word is not part of a place name ("Berlin ASAP" -> "Berlin").
        words = m.group(1).split()
        stop = {"asap", "urgent", "express", "today", "immediately", "now", "please"}
        kept = []
        for w in words:
            if w.lower() in stop:
                break
            kept.append(w)
        dest = " ".join(kept) or None

    summary = f"{intent_type} request for {len(items)} line item(s)"
    return {
        "intent_type": intent_type,
        "items": items,
        "destination": dest,
        "urgency": urgency,
        "constraints": ["bulk pricing required"] if intent_type == "bulk_order" else [],
        "summary": summary,
    }


# --------------------------------------------------------------------------- #
# Task extraction (inbox mail + customer form)
# --------------------------------------------------------------------------- #

# An action starts with one of these verbs, a checkbox marker, a labelled
# TODO/TASK line, or a numbered list item. Kept deliberately explicit: this is
# the offline fallback, so it must never invent work out of polite small talk.
_TASK_START = re.compile(
    r"^(?:please|kindly|could you|can you|would you|need to|needs to|must|"
    r"remember to|make sure to|don't forget to|follow up|chase|review|approve|"
    r"check|verify|prepare|update|send|ship|deliver|process|confirm|escalate|"
    r"fix|return|order|call|email|handle|reconcile|sign|complete|arrange|book|"
    r"create|raise|submit)\b",
    re.IGNORECASE,
)
_MARKER = re.compile(r"^(?:[-*]\s*)?\[[ xX]\]\s*|^(?:TODO|TASK|ACTION)\s*[:.-]\s*", re.IGNORECASE)
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+")
_INLINE_REQUEST = re.compile(r"\b(?:please|kindly|could you|can you|need to)\b", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_ASSIGNEE = re.compile(
    r"(?:assign(?:ed)?\s+to|owner\s*[:=]|for)\s+([\w.+-]+@[\w-]+\.[\w.-]+)",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
_SLASH_DATE = re.compile(r"\bby\s+(\d{1,2}/\d{1,2}/20\d{2})\b", re.IGNORECASE)
_NAMED_DATE = re.compile(
    r"\bby\s+(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    re.IGNORECASE,
)
_LEAD = re.compile(
    r"^(?:please|kindly|could you|can you|would you|need to|needs to|"
    r"remember to|make sure to|don't forget to)\s+",
    re.IGNORECASE,
)
_HIGH = re.compile(
    r"\b(urgent(?:ly)?|asap|immediately|critical|today|eod|end of day|high priority|priority high)\b",
    re.IGNORECASE,
)
_LOW = re.compile(r"\b(no rush|low priority|whenever|when you can|next week)\b", re.IGNORECASE)
_FORM_KEYS = ("task", "action", "request", "description", "notes", "items", "details")


def _task_units(body: str, fields: dict[str, str]) -> list[str]:
    """Split free text + form fields into candidate action lines."""
    units: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        marker = _MARKER.match(line)
        if marker:
            line = line[marker.end():]
        else:
            numbered = _NUMBERED.match(line)
            if numbered:
                line = line[numbered.end():]
        if not line:
            continue
        if marker or numbered or _TASK_START.match(line):
            units.append(line)
            continue
        inline = _INLINE_REQUEST.search(line)
        if inline and inline.start() < len(line) * 0.6:
            units.append(line[inline.start():])

    for key, value in fields.items():
        if value and str(key).lower() in _FORM_KEYS:
            units.append(f"{key}: {value}")
    return units


def _title(unit: str) -> str:
    title = _LEAD.sub("", unit).strip(" \t-•;:")
    title = re.sub(r"\s+", " ", title)
    return title[:140].rstrip(" .,;:?!")


def _priority(unit: str) -> str:
    if _HIGH.search(unit):
        return "high"
    if _LOW.search(unit):
        return "low"
    return "normal"


def _due_date(unit: str) -> str | None:
    match = _ISO_DATE.search(unit)
    if match:
        return match.group(1)
    match = _SLASH_DATE.search(unit)
    if match:
        for pattern in ("%d/%m/%Y", "%m/%d/%Y"):
            try:
                return datetime.strptime(match.group(1), pattern).date().isoformat()
            except ValueError:
                continue
    match = _NAMED_DATE.search(unit)
    return match.group(0).lower() if match else None


def _assignee(unit: str, default: str) -> str:
    match = _ASSIGNEE.search(unit)
    if match:
        return match.group(1)
    return default.strip()


def extract_tasks(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract actionable tasks from an email body or a customer form.

    Deterministic and offline: checkbox lists, numbered lists, imperative
    requests and labelled form fields become tasks. Priority, due date and
    assignee are read from the same line. The LLM path uses the same schema.
    """
    body = str(payload.get("body") or "")
    if not body and payload.get("text"):
        body = str(payload["text"])
    fields = {
        str(key): str(value)
        for key, value in (payload.get("form_fields") or {}).items()
        if value not in (None, "")
    }
    default_assignee = str(payload.get("assignee_email") or "")

    tasks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for unit in _task_units(body, fields):
        title = _title(unit)
        if not title:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        tasks.append({
            "title": title,
            "description": "",
            "assignee_email": _assignee(unit, default_assignee) or None,
            "priority": _priority(unit),
            "due_date": _due_date(unit),
        })

    if not tasks and fields:
        summary = ", ".join(f"{key}: {value}" for key, value in list(fields.items())[:4])
        tasks.append({
            "title": _title(summary)[:140],
            "description": "Extracted from a customer form submission.",
            "assignee_email": default_assignee or None,
            "priority": _priority(str(fields)),
            "due_date": _due_date(str(fields)),
        })

    return {"tasks": tasks}


def develop_clearance_strategy(payload: dict[str, Any]) -> dict[str, Any]:
    stock = payload["stock"]
    age_days: int = stock["age_days"]
    on_hand: int = stock["on_hand"]
    overstocked = on_hand >= 80
    aging = age_days >= 90

    if aging or overstocked:
        discount = 30.0 if (aging and overstocked) else 20.0 if aging else 15.0
    else:
        discount = 0.0

    # bulk demand clears stock faster than a discount does
    if payload.get("quantity", 1) >= 20:
        discount = max(discount - 5.0, 0.0)

    substitutes = [s["sku"] for s in payload.get("substitutes", [])][:2]
    rationale = (
        f"on_hand={on_hand}, age_days={age_days}: "
        + ("clearance discount proposed" if discount else "healthy stock, sell at list price")
    )
    return {
        "sku": payload["sku"],
        "discount_pct": discount,
        "bundle_with": [],
        "substitute_skus": substitutes,
        "rationale": rationale,
    }


_CHEAP_CARRIERS = ["RegionalPost", "EcoFreight"]
_FAST_CARRIERS = ["SwiftExpress", "AirNexus"]


# --------------------------------------------------------------------------- #
# Advertisement copy
# --------------------------------------------------------------------------- #

# Where a clearance strategy becomes a shareable post. The rule mirrors what the
# prompt asks the model for, so the renderer draws either output identically.

def write_advert_copy(payload: dict[str, Any]) -> dict[str, Any]:
    """Headline / tagline / badge / CTA for one offer, per tone."""
    name = str(payload.get("product_name") or "this item").strip()
    old = _money(payload.get("old_price"))
    new = _money(payload.get("new_price"))
    currency = str(payload.get("currency") or "$")
    category = str(payload.get("category") or "").strip()
    tone = str(payload.get("tone") or "bold").lower()

    pct = round((1 - new / old) * 100) if old > 0 and new < old else 0
    saving = max(old - new, 0.0)
    save_text = f"{currency}{saving:,.2f}"
    badge = f"-{pct}%" if pct else "OFFER"

    if tone == "minimal":
        headline = f"{pct}% off" if pct else "New price"
        tagline = "Limited stock. No code needed."
        cta = "Shop now"
    elif tone == "playful":
        headline = "SNAP IT UP" if pct else "JUST LANDED"
        tagline = "Before someone else does." if pct else "Fresh on the shelf."
        cta = "Grab yours"
    elif tone == "premium":
        headline = "FINAL EDITION" if pct else "THE NEW SEASON"
        tagline = (
            f"An archival price — {save_text} below list." if pct else "Quietly exceptional."
        )
        cta = "Discover"
    else:  # bold
        headline = "PRICE DROP" if pct else "NOW IN STOCK"
        tagline = f"Save {save_text} while stock lasts." if pct else "Ready to ship today."
        cta = "Shop now"

    tags = ["clearance", "sale", "offer"]
    if category:
        slug = "".join(ch for ch in category.lower() if ch.isalnum())
        if slug:
            tags.insert(0, slug)
    if pct:
        tags.append(f"{pct}off")

    return {
        "badge": badge,
        "headline": headline,
        "subheadline": name,
        "tagline": tagline,
        "cta": cta,
        "hashtags": list(dict.fromkeys(tags))[:5],
    }


def _money(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def write_transit_copy(payload: dict[str, Any]) -> dict[str, Any]:
    """Headline / badge / CTA for a shipping notice, per tone.

    Same shape as :func:`write_advert_copy` so the poster and the notice share
    one copy contract; the numbers here are ETAs rather than prices.
    """
    carrier = str(payload.get("carrier") or "carrier").strip()
    service = str(payload.get("service_level") or "").strip()
    destination = str(payload.get("destination") or "your address").strip()
    shipment = str(payload.get("shipment_id") or "").strip()
    eta = _money(payload.get("eta_days"))
    items = [str(name).strip() for name in payload.get("items") or [] if str(name).strip()]
    quantity = int(_money(payload.get("quantity")) or 1)
    tone = str(payload.get("tone") or "bold").lower()

    days = round(eta)
    arrival = "today" if eta <= 0 else ("tomorrow" if days <= 1 else f"in {days} days")
    goods = " · ".join(items) if items else "your order"
    if quantity > 1:
        goods = f"{quantity} x {goods}"

    if tone == "minimal":
        badge = "IN TRANSIT"
        headline = f"Arriving {arrival}"
        tagline = f"{carrier}{f' {service}' if service else ''} · {destination}"
        cta = "Track parcel"
    elif tone == "playful":
        badge = "ON THE MOVE"
        headline = "ALMOST THERE!"
        tagline = f"{carrier} is bringing it {arrival} — nearly yours."
        cta = "Follow it"
    elif tone == "premium":
        badge = "IN TRANSIT"
        headline = "EN ROUTE"
        tagline = f"{carrier}{f' {service}' if service else ''} · arriving {arrival} in {destination}"
        cta = "Track delivery"
    else:  # bold
        badge = "IN TRANSIT"
        headline = "ON ITS WAY"
        tagline = (
            f"{carrier}{f' {service}' if service else ''} · arriving {arrival} · {destination}"
        )
        cta = "Track parcel"

    return {
        "badge": badge,
        "headline": headline,
        "subheadline": goods,
        "tagline": tagline,
        "cta": cta,
        "tracking": shipment,
        "hashtags": [],
    }



def plan_route(payload: dict[str, Any]) -> dict[str, Any]:
    urgency: str = payload.get("urgency", "normal")
    quantity: int = payload.get("quantity", 1)
    destination: str = payload.get("destination") or "domestic"
    international = any(w in destination.lower() for w in ("abroad", "overseas", "intl", "us", "eu", "china"))

    if urgency == "high":
        carrier, service, days, cost, risk = "SwiftExpress", "next-day", 1.0, 18.5, "low"
    else:
        carrier, service, days, cost, risk = "RegionalPost", "standard", 3.0, 6.5, "low"

    if quantity >= 50:
        carrier, service, days, cost = "EcoFreight", "pallet-freight", 5.0, 42.0
        risk = "medium"
    if international:
        carrier, service, days, cost, risk = "AirNexus", "international-express", 4.0, 65.0, "medium"

    rationale = f"urgency={urgency}, qty={quantity}, dest={destination} -> {carrier}/{service}"
    return {
        "carrier": carrier,
        "service_level": service,
        "eta_days": days,
        "cost": cost,
        "risk": risk,
        "rationale": rationale,
    }


def resolve_exception(payload: dict[str, Any]) -> dict[str, Any]:
    exception: str = payload["exception"]
    plan: dict[str, Any] = payload.get("plan", {})

    if exception == "damaged":
        return {
            "action": "replace",
            "carrier": plan.get("carrier"),
            "eta_days": 3.0,
            "cost": 12.0,
            "rationale": "damaged goods: replacement is cheaper than a dispute; carrier claim filed separately",
        }
    if exception == "lost":
        return {
            "action": "refund",
            "carrier": None,
            "eta_days": None,
            "cost": float(payload.get("order_value", 0.0)),
            "rationale": "lost parcel: refund and re-order only if the customer confirms",
        }
    if exception == "wrong_address":
        return {
            "action": "reroute",
            "carrier": plan.get("carrier"),
            "eta_days": float(plan.get("eta_days", 3.0)) + 1.0,
            "cost": 8.0,
            "rationale": "address corrected at the hub; no re-dispatch needed",
        }
    # carrier_delay / customs_hold
    return {
        "action": "reroute",
        "carrier": _FAST_CARRIERS[0] if exception == "carrier_delay" else plan.get("carrier"),
        "eta_days": 2.0,
        "cost": 15.0,
        "rationale": f"{exception}: alternative lane available at acceptable cost",
    }


_DAMAGED_WORDS = {"damaged", "broken", "defective", "faulty", "cracked", "unusable", "not working"}
_REFUND_WORDS = {"refund", "return", "money back", "reimburse", "compensate"}
_REPLACE_WORDS = {"replace", "replacement", "exchange", "send another", "send a new"}


def assess_return(payload: dict[str, Any]) -> dict[str, Any]:
    """Decide refund / replacement / reject from the return reason and policy."""
    reason = str(payload.get("reason", "")).lower()
    quantity = int(payload.get("quantity", 1))
    order_value = float(payload.get("order_value", 0.0))
    days = int(payload.get("days_since_delivery", 0))
    policy = payload.get("policy", {}) or {}
    window = int(policy.get("window_days", 30))
    fee_pct = float(policy.get("restocking_fee_pct", 0.0))
    damaged = any(w in reason for w in _DAMAGED_WORDS)
    lost = any(w in reason for w in ("lost", "never received", "missing"))
    wants_refund = any(w in reason for w in _REFUND_WORDS) or lost
    wants_replacement = any(w in reason for w in _REPLACE_WORDS)

    eligible = days <= window or (damaged and bool(policy.get("damaged_always_eligible", True)))
    if not eligible:
        return {
            "action": "reject",
            "eligible": False,
            "refund_amount": 0.0,
            "restocking_fee": 0.0,
            "rationale": f"{days} days since delivery exceeds the {window}-day return window",
        }

    fee = min(order_value, round(order_value * fee_pct / 100.0, 2))
    amount = max(0.0, round(order_value - fee, 2))
    action = "replacement" if (wants_replacement and not wants_refund) else "refund"
    rationale = (
        f"{quantity} unit(s), reason='{reason or 'unspecified'}': {action} "
        f"(order value ${order_value:.2f}, fee ${fee:.2f}, window {window}d)"
    )
    return {
        "action": action,
        "eligible": True,
        "refund_amount": amount if action == "refund" else 0.0,
        "restocking_fee": fee if action == "refund" else 0.0,
        "rationale": rationale,
    }


def build_rule_llm() -> RuleBasedLLM:
    llm = RuleBasedLLM()
    llm.register("capture_intent", capture_intent)
    llm.register("extract_tasks", extract_tasks)
    llm.register("develop_clearance_strategy", develop_clearance_strategy)
    llm.register("plan_route", plan_route)
    llm.register("resolve_exception", resolve_exception)
    llm.register("assess_return", assess_return)
    llm.register("write_advert_copy", write_advert_copy)
    llm.register("write_transit_copy", write_transit_copy)
    return llm

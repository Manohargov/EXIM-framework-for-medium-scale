"""Build the MSM Developer Document PDF from source-grounded content."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "docs" / "user-guide" / "screenshots"
ARCH = ROOT / "docs" / "architecture"
OUTPUT = ROOT / "docs" / "developer-doc" / "MSM_Developer_Document.pdf"

GOLD = colors.HexColor("#b08d4f")
INK = colors.HexColor("#1a1a1c")
MUTED = colors.HexColor("#5d5d63")
LIGHT = colors.HexColor("#f5f2eb")
CODE_BG = colors.HexColor("#f2f0eb")

ss = getSampleStyleSheet()
ss.add(ParagraphStyle("DevCoverTitle", parent=ss["Title"], fontName="Helvetica-Bold",
                      fontSize=32, leading=38, textColor=INK, spaceAfter=10))
ss.add(ParagraphStyle("DevCoverSub", parent=ss["Normal"], fontName="Helvetica",
                      fontSize=14, leading=20, textColor=MUTED, spaceAfter=8))
ss.add(ParagraphStyle("DevH1", parent=ss["Heading1"], fontName="Helvetica-Bold",
                      fontSize=19, leading=23, textColor=GOLD, spaceBefore=8,
                      spaceAfter=9, keepWithNext=True))
ss.add(ParagraphStyle("DevH2", parent=ss["Heading2"], fontName="Helvetica-Bold",
                      fontSize=12.5, leading=16, textColor=INK, spaceBefore=9,
                      spaceAfter=4, keepWithNext=True))
ss.add(ParagraphStyle("DevBody", parent=ss["BodyText"], fontName="Helvetica",
                      fontSize=9, leading=13, textColor=INK, spaceAfter=5))
ss.add(ParagraphStyle("DevBullet", parent=ss["DevBody"], leftIndent=14,
                      bulletIndent=4, spaceAfter=2))
ss.add(ParagraphStyle("DevCaption", parent=ss["Normal"], fontName="Helvetica-Oblique",
                      fontSize=8, leading=11, textColor=MUTED, alignment=TA_CENTER,
                      spaceAfter=9))
ss.add(ParagraphStyle("DevCode", parent=ss["Normal"], fontName="Courier",
                      fontSize=7.6, leading=10.2, textColor=INK,
                      backColor=CODE_BG, borderPadding=5, spaceAfter=7))
ss.add(ParagraphStyle("DevSmall", parent=ss["Normal"], fontName="Helvetica",
                      fontSize=8, leading=11, textColor=MUTED))


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def p(text: str, style: str = "DevBody"):
    return Paragraph(text, ss[style])


def bullets(items: list[str]):
    return [Paragraph(f"<bullet>&bull;</bullet> {esc(i)}", ss["DevBullet"]) for i in items]


def code(text: str):
    return Paragraph(esc(text).replace("\n", "<br/>"), ss["DevCode"])


def table(rows, widths, header=True, font=8):
    t = Table(rows, colWidths=[w * inch for w in widths], repeatRows=1 if header else 0)
    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", font),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9c2b4")),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), GOLD),
                  ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                  ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", font)]
    t.setStyle(TableStyle(style))
    return t


def shot(name, caption, max_h=3.8):
    path = SHOTS / name
    if not path.exists():
        return p(f"[missing screenshot {name}]", "DevSmall")
    img = Image(str(path))
    ratio = min(6.3 * inch / img.imageWidth, max_h * inch / img.imageHeight, 1.0)
    img.drawWidth, img.drawHeight = img.imageWidth * ratio, img.imageHeight * ratio
    return KeepTogether([img, p(caption, "DevCaption")])


def diagram(name, caption):
    path = ARCH / name
    if not path.exists():
        return p(f"[missing diagram {name}]", "DevSmall")
    img = Image(str(path))
    ratio = min(6.3 * inch / img.imageWidth, 2.9 * inch / img.imageHeight, 1.0)
    img.drawWidth, img.drawHeight = img.imageWidth * ratio, img.imageHeight * ratio
    return KeepTogether([img, p(caption, "DevCaption")])


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.8 * inch, 0.55 * inch, "MSM - Developer Document")
    canvas.drawRightString(A4[0] - 0.8 * inch, 0.55 * inch, f"Page {doc.page}")
    canvas.setStrokeColor(colors.HexColor("#ddd7cc"))
    canvas.line(0.8 * inch, 0.72 * inch, A4[0] - 0.8 * inch, 0.72 * inch)
    canvas.restoreState()


def build():
    doc = BaseDocTemplate(str(OUTPUT), pagesize=A4,
                          leftMargin=0.8 * inch, rightMargin=0.8 * inch,
                          topMargin=0.7 * inch, bottomMargin=0.8 * inch,
                          title="MSM Developer Document", author="MSM Engineering")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="dev")
    doc.addPageTemplates([PageTemplate(id="dev", frames=[frame], onPage=footer)])
    s: list = []

    # Cover
    s.append(Spacer(1, 0.9 * inch))
    s.append(p("MSM Operations Console", "DevCoverTitle"))
    s.append(p("Developer Document - Architecture, Data, UI/UX, AI Integration and Runtime", "DevCoverSub"))
    s.append(Spacer(1, 0.2 * inch))
    cover = table([
        ["Package", "ecomops 0.4.0"],
        ["Runtime", "Python 3.14.8, Streamlit 1.67.0, FastAPI (optional)"],
        ["Repository", "Local git repo in msm/ (no remote)"],
        ["Console", "http://localhost:8501"],
        ["Docs", "README.md, docs/user-guide, docs/architecture"],
    ], [1.4, 5.0], header=False)
    cover.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), LIGHT)]))
    s.append(cover)
    s.append(Spacer(1, 0.3 * inch))
    s.append(p(
        "This document is source-grounded: every schema, module and flow described here "
        "is implemented in <font face='Courier'>msm/src/ecomops</font>. It is intended "
        "for developers who need to understand, extend or deploy the system.", "DevBody"))
    s.append(NextPageTemplate("dev"))
    s.append(PageBreak())

    # Contents
    s.append(p("Contents", "DevH1"))
    s.extend(bullets([
        "1. System architecture and composition",
        "2. Module and file map",
        "3. Data model and schemas (Pydantic)",
        "4. Storage model - event bus, journal, session state, files",
        "5. UI/UX architecture and design system",
        "6. AI/ML model integration",
        "7. Runtime flows and event choreography",
        "8. API, CLI and deployment",
        "9. Testing, security and operations",
        "Appendix A - Architecture diagrams",
        "Appendix B - Configuration reference",
    ]))
    s.append(PageBreak())

    # 1 Architecture
    s.append(p("1. System architecture and composition", "DevH1"))
    s.append(p(
        "MSM is a single-process, event-driven console. The Streamlit UI, CLI and "
        "optional FastAPI service share one <b>EcomopsSystem</b> composition root. "
        "That root owns an in-process EventBus, an ApprovalGate, an LLM interface, an "
        "embedder and five agents. All coordination is via typed events; no agent calls "
        "another directly. Side effects pass through the approval gate.", "DevBody"))
    s.append(diagram("msm-architecture.png", "Figure 1 - Complete system architecture (editable draw.io source in docs/architecture)"))
    s.append(p("1.1 Entry points", "DevH2"))
    s.append(table([
        ["Entry", "Module", "Purpose"],
        ["Console", "app.py", "Main operator UI on port 8501"],
        ["CLI", "cli.py", "demo, request, exceptions, stock, returns, catalogue, replay, serve, ui"],
        ["API", "api.py", "Optional FastAPI control plane with bearer auth"],
        ["Deployment agent", "deploy_agent/", "Deep Agents automation for local deployment"],
    ], [1.1, 1.2, 4.0]))
    s.append(Spacer(1, 6))
    s.append(p("1.2 Composition root", "DevH2"))
    s.append(code(
        "EcomopsSystem(\n"
        "  bus=EventBus(), llm=build_rule_llm(), embedder=TokenOverlapEmbedder(),\n"
        "  policy=PolicyConfig(), return_policy=ReturnPolicy(),\n"
        ")\n"
        "__post_init__:\n"
        "  inventory = Inventory.with_catalogue(embedder, catalogue)\n"
        "  gate, customer, clearance, transit, returns, tasks = ...\n"
        "  for component in (...): component.attach()\n"
        "  journal.attach(bus)  # when configured"))
    s.append(PageBreak())

    # 2 File map
    s.append(p("2. Module and file map", "DevH1"))
    s.append(table([
        ["Module", "Responsibility"],
        ["app.py", "Streamlit shell, theme CSS, navigation, 10 workspace renderers"],
        ["theme.py", "Six CSS-variable palettes, glass/flat tokens, contrast override"],
        ["system.py", "Composition root and inbound entry points"],
        ["events.py", "Typed event catalogue and shared Pydantic data models"],
        ["bus.py", "Publish/subscribe, history, correlation/causation, drain"],
        ["policy.py", "Approval gate, risk thresholds, human decisions"],
        ["llm.py", "LLM protocol, RuleBasedLLM, FallbackLLM, MlxLLM, embedders"],
        ["rules.py", "Eight deterministic handlers (the offline model)"],
        ["agents/*.py", "Customer, clearance, transit, returns and task-extraction agents"],
        ["inventory.py", "Catalogue, stock, ageing, search and SKU matching"],
        ["finance.py", "Revenue/refund and referral read models"],
        ["metrics.py", "Event-derived counters and Prometheus text"],
        ["mail.py", "IMAP reader and MIME parsing (injectable client)"],
        ["tasks.py", "TaskView fold, status transitions, user scoping"],
        ["chat.py", "OpsChat: rule intents, role-aware snapshot, grounding"],
        ["chatmodel.py", "Chat connector: off/endpoint/gguf, llama-server lifecycle"],
        ["documents/*", "PDF/image extraction, classification, field resolution, fill"],
        ["advert.py", "Pillow poster/notice renderer, collage layouts"],
        ["webimages.py", "Openverse/Wikimedia image search and disk cache"],
        ["promotion.py", "Publishing channels and dispatch reports"],
        ["api.py", "FastAPI endpoints and auth dependencies"],
        ["deploy_agent/*", "Deep Agents deployment automation and safety tools"],
    ], [1.7, 4.6], font=7.8))
    s.append(PageBreak())

    # 3 Data model
    s.append(p("3. Data model and schemas", "DevH1"))
    s.append(p(
        "All domain data is Pydantic v2. Events share an envelope: event_id, "
        "correlation_id, causation_id, source and ts. model_config forbids extra "
        "fields. event_classes() walks subclasses, so new events auto-register for "
        "the journal and API.", "DevBody"))
    s.append(p("3.1 Event envelope and enumerations", "DevH2"))
    s.append(code(
        "class Event(BaseModel):\n"
        "    topic: ClassVar[str] = 'Event'\n"
        "    event_id: str = Field(default_factory=new_id)\n"
        "    correlation_id: str | None = None\n"
        "    causation_id: str | None = None\n"
        "    source: str = 'system'\n"
        "    ts: datetime = Field(default_factory=utcnow)\n"
        "\n"
        "IntentType = purchase | delivery_status | return | complaint | bulk_order | other\n"
        "Urgency    = low | normal | high\n"
        "RiskLevel  = low | medium | high"))
    s.append(p("3.2 Core event catalogue", "DevH2"))
    s.append(table([
        ["Topic", "Key payload", "Producer"],
        ["CustomerRequestReceived", "customer_id, request_text, channel", "UI / CLI / API"],
        ["CustomerIntentCaptured", "customer_id, intent, matched, llm_backend", "Customer agent"],
        ["DemandSignal", "customer_id, sku, product_name, quantity, urgency", "Customer agent"],
        ["StockLevelReport", "overstocked skus, totals", "UI / CLI"],
        ["ClearanceStrategyProposed", "strategy (sku, discount_pct, bundle)", "Clearance agent"],
        ["DiscountApplied / StockReserved", "sku, prices / quantity", "Approval gate -> agent"],
        ["ShipmentRouted", "shipment_id, plan, destination, quantity", "Transit agent"],
        ["DeliveryExceptionReported / Resolved", "shipment_id, exception, resolution", "UI / webhook"],
        ["ReturnRequested / ReturnAssessed", "shipment_id, assessment, action", "Returns agent"],
        ["RefundIssued / ReplacementAuthorised", "amount / shipment", "Returns agent"],
        ["ActionProposed / Approved / Rejected", "proposal_id, action, parameters", "Gate / operator"],
        ["EmailReceived / FormReceived", "sender/body or text/fields, assignee", "UI / IMAP"],
        ["TaskExtracted", "task_id, title, assignee_email, priority, due_date, status", "Task agent"],
        ["TaskStatusChanged", "task_id, status, previous_status, changed_by", "UI"],
        ["DocumentUploaded / FieldsResolved / Filled", "job metadata (never bytes)", "Document pipeline"],
        ["TransitNoticeSent / PromotionPublished", "channel, recipients, content", "promotion.py"],
    ], [2.0, 3.0, 1.3], font=7.5))
    s.append(Spacer(1, 6))
    s.append(p("3.3 Task and chat models", "DevH2"))
    s.append(code(
        "ExtractedTask: title, description='', assignee_email=None,\n"
        "               priority='normal', due_date=None\n"
        "TaskList:      tasks: list[ExtractedTask]\n"
        "\n"
        "TaskView (frozen): task_id, title, description, assignee_email, priority,\n"
        "    due_date, status, source, source_event_id, sender, created_at, updated_at\n"
        "\n"
        "MailConfig: host, port, username, password(repr=False), folder,\n"
        "    limit, use_ssl, mark_seen\n"
        "MailMessage: message_id, sender, recipients, subject, date, body\n"
        "\n"
        "ChatModelConfig: mode(off|endpoint|gguf), base_url, model,\n"
        "    api_key(repr=False), gguf_path, llama_server, port, context,\n"
        "    gpu_layers, timeout, max_tokens"))
    s.append(PageBreak())

    # 3.4 Document schema
    s.append(p("3.4 Document intake schema", "DevH1"))
    s.append(p(
        "Documents are classified into purchase orders, return slips, stock sheets or "
        "generic forms. <b>FieldSpec</b> patterns extract labelled values; "
        "<b>DocField</b> tracks extracted/resolved values, source, confidence, status "
        "and optional bounding box.", "DevBody"))
    s.append(table([
        ["DocType", "Fields"],
        ["purchase_order", "po_number, order_date, customer, ship_to, requested_delivery?, order_total"],
        ["return_slip", "return_id, order_id?, shipment_id?, sku, quantity, reason, refund_total?, customer?"],
        ["stock_sheet", "sku, description, on_hand, unit_cost, markdown_pct?, location?"],
        ["generic", "Label: value lines (up to 10), no fixed schema"],
    ], [1.4, 4.9], font=7.8))
    s.append(Spacer(1, 6))
    s.append(code(
        "DocJob: id, filename, sha256, kind, doc_type, pages, fields[],\n"
        "        status(extracted|review|awaiting_input|filled),\n"
        "        created_at, output_path\n"
        "DocField.status: extracted | known | ambiguous | missing | not_found\n"
        "FieldStatus answer = resolved if present else extracted value\n"
        "blocking = required AND needs_input"))
    s.append(PageBreak())

    # 4 Storage
    s.append(p("4. Storage model", "DevH1"))
    s.append(p(
        "There is no relational database. The event bus is the source of truth for "
        "domain state; the journal is an append-only audit log; the UI keeps ephemeral "
        "state in Streamlit session_state; the catalogue is a CSV; images and document "
        "workdirs are files on disk.", "DevBody"))
    s.append(table([
        ["Store", "Technology", "Contents", "Lifetime"],
        ["EventBus", "In-memory list", "Typed events with correlation/causation", "Process lifetime"],
        ["EventJournal", "JSONL file", "Serialised event records", "Durable (opt-in)"],
        ["session_state", "Streamlit server-side", "user, view, theme, chat history, doc session, mail/chat config", "Browser session"],
        ["Inventory", "In-memory dict", "Products from built-in SKUs + optional grocery CSV", "Process lifetime"],
        ["Catalogue CSV", "data/grocery_catalogue.csv", "1,481 grocery products", "On disk"],
        ["Image cache", "data/image_cache", "Downloaded web images keyed by sha1", "On disk, regenerable"],
        ["Document workdirs", "tempfile.mkdtemp()", "Uploaded PDF/images and filled output (0700)", "Until discard/finish"],
        ["Task state", "Derived from events", "Fold of TaskExtracted + TaskStatusChanged", "Process lifetime"],
    ], [1.25, 1.25, 2.5, 1.3], font=7.4))
    s.append(Spacer(1, 6))
    s.append(p(
        "Task current state is a pure fold: start from each TaskExtracted for the "
        "assignee, then apply every TaskStatusChanged with matching task_id. Status "
        "changes are never written in place.", "DevBody"))
    s.append(code(
        "my_tasks(bus, email, all_users=False) -> list[TaskView]\n"
        "change_status(bus, task_id, status, changed_by) -> publishes TaskStatusChanged\n"
        "system.my_tasks(...) / system.update_task_status(...) delegate to tasks.py"))
    s.append(PageBreak())

    # 5 UI/UX
    s.append(p("5. UI / UX architecture", "DevH1"))
    s.append(p(
        "The console is a single Streamlit page with a custom shell. Navigation moved "
        "from top tabs to a top-right icon rail; the sidebar keeps tools. Each view is "
        "a renderer function in app.py, dispatched by st.session_state['view'] and "
        "re-validated against permissions on every rerun.", "DevBody"))
    s.append(shot("02-overview.jpeg", "Figure 2 - Console shell: icon rail, compact KPI strip, section headers"))
    s.append(p("5.1 Design tokens", "DevH2"))
    s.append(p(
        "theme.py injects a &lt;style&gt; block of CSS custom properties after the base "
        "stylesheet so palette variables win the cascade. Every component consumes "
        "those variables, so switching themes never rebuilds widgets.", "DevBody"))
    s.append(table([
        ["Token group", "Examples", "Use"],
        ["Surfaces", "--msm-ink, --msm-surface, --msm-panel, --msm-field", "Page, cards, sidebar, inputs"],
        ["Text", "--msm-ivory, --msm-muted, --msm-dim, --msm-faint", "Primary/secondary text"],
        ["Accents", "--msm-gold, --msm-accent-2/3/4", "Four-hue rule, stripes, badges"],
        ["Glass", "--msm-glass, --msm-glass-strong, --msm-glass-border, --msm-shadow", "Frosted panels"],
        ["Blur", "--msm-blur, --msm-blur-strong, --msm-blur-mid", "none for flat themes"],
    ], [1.2, 2.9, 2.2], font=7.5))
    s.append(Spacer(1, 6))
    s.append(p("5.2 Layout and components", "DevH2"))
    s.extend(bullets([
        "Top-right: ten 3D icon buttons (hover tooltip via help=label), Theme pill, fixed positioning, four-hue border cycle.",
        "Header: compact wordmark, active view label and backend/role meta; KPI strip below with label+number only.",
        "Sidebar: user chip, Assistant toggle, Configuration/About expanders, Sign out; collapsed by default assistant.",
        "Forms/cards use 14px radius, theme-driven glass/flat material and soft borders.",
        "Task cards and document rows escape all interpolated values with html.escape before unsafe_allow_html.",
    ]))
    s.append(shot("03-theme-glass.jpeg", "Figure 3 - Glass palette"))
    s.append(shot("04-theme-flat.jpeg", "Figure 4 - Flat palette"))
    s.append(PageBreak())

    # 6 AI/ML
    s.append(p("6. AI / ML model integration", "DevH1"))
    s.append(p(
        "The system is deterministic-first. The domain LLM interface has eight "
        "registered tasks; rules always work offline. An optional local MLX model can "
        "replace rules through FallbackLLM. The sidebar assistant has a separate "
        "OpenAI-compatible connector with number grounding.", "DevBody"))
    s.append(p("6.1 Domain LLM", "DevH2"))
    s.append(table([
        ["Task", "Handler", "Consumer"],
        ["capture_intent", "rules.capture_intent", "CustomerRequirementsAgent"],
        ["extract_tasks", "rules.extract_tasks", "TaskExtractionAgent"],
        ["develop_clearance_strategy", "rules.develop_clearance_strategy", "StockClearanceAgent"],
        ["plan_route", "rules.plan_route", "TransitManagementAgent"],
        ["resolve_exception", "rules.resolve_exception", "TransitManagementAgent"],
        ["assess_return", "rules.assess_return", "ReturnsAgent"],
        ["write_advert_copy", "rules.write_advert_copy", "Stock clearance UI"],
        ["write_transit_copy", "rules.write_transit_copy", "Transit UI"],
    ], [2.1, 2.6, 1.6], font=7.5))
    s.append(Spacer(1, 5))
    s.append(code(
        "build_system(use_mlx=False) -> RuleBasedLLM\n"
        "build_system(use_mlx=True)  -> FallbackLLM(\n"
        "     primary=MlxLLM(model='mlx-community/Qwen3-4B-4bit'),\n"
        "     secondary=build_rule_llm())\n"
        "default_embedder() -> all-MiniLM-L6-v2 if sentence-transformers installed\n"
        "                      else TokenOverlapEmbedder"))
    s.append(p("6.2 Chat connector", "DevH2"))
    s.append(diagram("msm-chat-connector.png", "Figure 5 - Chat connector detail"))
    s.append(table([
        ["Mode", "Runtime", "Failure behaviour"],
        ["off", "None", "OpsChat uses deterministic intents and summary"],
        ["endpoint", "OpenAI-compatible URL; model discovered from /models or explicit", "Rules fallback"],
        ["gguf", "Starts llama-server for a .gguf path on demand; stops own process", "Rules fallback"],
    ], [1.0, 3.6, 1.7], font=7.6))
    s.append(p(
        "Grounding rejects model text whose numbers are absent from the JSON snapshot. "
        "Credentials come from env or a local auth file and are hidden in reprs.", "DevBody"))
    s.append(shot("16-assistant.jpeg", "Figure 6 - Assistant panel"))
    s.append(PageBreak())

    # 7 Flows
    s.append(p("7. Runtime flows and event choreography", "DevH1"))
    s.append(p("7.1 Customer request", "DevH2"))
    s.append(code(
        "UI form -> system.receive_customer_request(customer_id, text)\n"
        "  publish CustomerRequestReceived -> bus.drain()\n"
        "  CustomerRequirementsAgent.ask('capture_intent', schema=CustomerIntent)\n"
        "  inventory.match(query) -> SKU resolution\n"
        "  publish CustomerIntentCaptured\n"
        "  unresolved purchase lines -> IntentNeedsClarification\n"
        "  resolved purchase lines -> DemandSignal (one per item)\n"
        "  ClearanceAgent proposes discounts -> ActionProposed -> ApprovalGate\n"
        "  operator grants -> ActionApproved -> agent.act() -> DiscountApplied/StockReserved"))
    s.append(p("7.2 Email / form task extraction", "DevH2"))
    s.append(code(
        "IMAP -> mail.fetch(config) -> MailMessage[]\n"
        "system.receive_email(sender, body, subject, assignee_email)\n"
        "  publish EmailReceived -> bus.drain()\n"
        "TaskExtractionAgent.ask('extract_tasks', schema=TaskList)\n"
        "  publish TaskExtracted per task + TaskExtractionCompleted\n"
        "tasks.my_tasks(bus, user.email) folds status changes -> dashboard"))
    s.append(p("7.3 Approval gate", "DevH2"))
    s.append(code(
        "propose(action, params, reason, estimated_value) -> ActionProposed\n"
        "ApprovalRequested -> operator decides -> ActionApproved | ActionRejected\n"
        "on_approved -> act(proposal_id, action, params + _approved_by)\n"
        "on_rejected -> EscalationRaised"))
    s.append(p("7.4 Document intake", "DevH2"))
    s.append(code(
        "upload -> validate(size/pages/type) -> extract(text+boxes)\n"
        "classify(text) -> DocType -> extract_fields(text) -> attach_boxes()\n"
        "resolve(system, user, job) -> known/asked/sources\n"
        "DocumentUploaded + DocumentFieldsResolved events (no bytes)\n"
        "operator answers -> apply_answers() -> finish() -> fill() -> PDF\n"
        "DocumentFilled event"))
    s.append(p("7.5 Poster generation", "DevH2"))
    s.append(code(
        "candidates = discounts -> proposals -> stock\n"
        "AdvertCopy + Offer/Notice + Photo[] -> advert.render_poster / render_notice\n"
        "poster_bytes() -> st.download_button\n"
        "publish_promotion() / publish_transit_notice() -> channel report + bus event"))
    s.append(PageBreak())

    # 8 API/CLI/deploy
    s.append(p("8. API, CLI and deployment", "DevH1"))
    s.append(p("8.1 CLI", "DevH2"))
    s.append(code(
        "ecomops demo [--approve yes] [--journal PATH]\n"
        "ecomops request '...' [--grocery PATH] [--mlx]\n"
        "ecomops exceptions | stock | returns | catalogue | replay\n"
        "ecomops ui [--grocery PATH]\n"
        "ecomops serve [--port 8000] [--no-auth]   # local demos only"))
    s.append(p("8.2 API", "DevH2"))
    s.append(p(
        "FastAPI app from api.py with SessionCodec bearer tokens, role guards on "
        "domain endpoints, document upload/answer/fill/list endpoints, event and "
        "metrics endpoints. All audit decisions use the authenticated user's email.", "DevBody"))
    s.append(table([
        ["Area", "Endpoints (summary)"],
        ["Auth", "POST /auth/login, bearer verification"],
        ["Domain", "POST /requests, /stock, /exceptions, /returns, /approvals/{id}"],
        ["Read", "GET /events, /metrics, /finance, /approvals, /shipments"],
        ["Documents", "POST /documents, /documents/{id}/answers, /fill; GET /documents"],
    ], [1.2, 5.1], font=7.6))
    s.append(p("8.3 Local deployment", "DevH2"))
    s.extend(bullets([
        "Dockerfile: python:3.14-slim + poppler + pip install -e '.[ui,api,chat,docs]'.",
        "docker-compose.yml: port 8501, env-driven SSO/session settings, image cache volume.",
        "deploy_agent/: Deep Agents SDK agent with read-only tools and DEPLOY-MSM confirmation for mutations.",
        "Git: local repo only; no remote push configured.",
    ]))
    s.append(PageBreak())

    # 9 Testing/security/ops
    s.append(p("9. Testing, security and operations", "DevH1"))
    s.append(p("9.1 Test suites", "DevH2"))
    s.append(table([
        ["File", "Coverage"],
        ["test_app.py", "Role tabs/nav, login gate, KPI rail, demo and return flows"],
        ["test_theme.py", "Six themes, four hues, glass/flat split, contrast, picker"],
        ["test_tasks.py", "Rule extraction, email/form pipeline, status fold, UI"],
        ["test_mail.py", "MIME parsing, config redaction, fake IMAP client"],
        ["test_chatmodel.py", "Config, modes, GGUF lifecycle, grounding"],
        ["test_deploy_agent.py", "Confirmation gates, path scoping, tool registration"],
        ["test_documents.py", "Extraction, classification, resolution, fill, audit"],
        ["test_system.py / test_bus.py", "Composition, LLM fallback, correlation/causation"],
    ], [1.7, 4.6], font=7.6))
    s.append(Spacer(1, 5))
    s.append(p("Verification at build time: ruff clean, 293 pytest passes.", "DevBody"))
    s.append(p("9.2 Security posture", "DevH2"))
    s.extend(bullets([
        "Dev mode with displayed demo password is for local use only; bind to 127.0.0.1 or add real SSO/TLS before network exposure.",
        "Set ECOMOPS_SESSION_SECRET for the API; default is a development placeholder.",
        "All user-derived HTML is escaped before unsafe_allow_html rendering.",
        "Uploaded filenames are sanitized; temp dirs are 0700; subprocess calls use argument lists.",
        "Mail and chat secrets are hidden in reprs and never written to the event journal.",
        "Deployment agent mutating tools require the phrase DEPLOY-MSM and never push to a remote.",
    ]))
    s.append(p("9.3 Health checks", "DevH2"))
    s.append(code(
        "curl http://localhost:8501/_stcore/health      # -> ok\n"
        ".venv/bin/ruff check src tests deploy_agent\n"
        ".venv/bin/python -m pytest -q\n"
        "git -C msm status --short                       # clean working tree"))
    s.append(PageBreak())

    # Appendix A
    s.append(p("Appendix A - Architecture diagrams", "DevH1"))
    s.append(diagram("msm-architecture.png", "Figure A1 - Full architecture"))
    s.append(Spacer(1, 10))
    s.append(diagram("msm-chat-connector.png", "Figure A2 - Chat connector"))
    s.append(PageBreak())

    # Appendix B
    s.append(p("Appendix B - Configuration reference", "DevH1"))
    s.append(table([
        ["Variable", "Default", "Effect"],
        ["ECOMOPS_SSO_MODE", "dev", "dev | oidc | proxy"],
        ["ECOMOPS_DEMO_PASSWORD", "msm", "Demo credential"],
        ["ECOMOPS_SESSION_SECRET", "dev placeholder", "API bearer HMAC key"],
        ["ECOMOPS_AUTH_AUTOLOGIN", "unset", "Test-only role bypass"],
        ["ECOMOPS_GROCERY", "auto", "CSV path or none to disable"],
        ["ECOMOPS_MAIL_HOST/PORT/USER/PASSWORD", "unset", "IMAP connection"],
        ["ECOMOPS_MAIL_FOLDER/LIMIT/SSL/MARK_SEEN", "INBOX/20/true/false", "IMAP behaviour"],
        ["ECOMOPS_CHAT_MODE", "endpoint", "off | endpoint | gguf"],
        ["ECOMOPS_CHAT_BASE_URL", "Lemonade 13305", "Chat endpoint"],
        ["ECOMOPS_CHAT_MODEL", "Llama-3.2-1B-Instruct-GGUF", "Model id"],
        ["ECOMOPS_CHAT_GGUF / PORT / CTX / NGL", "unset / 38285 / 8192 / -1", "GGUF runtime"],
        ["ECOMOPS_DEEPAGENTS_BASE_URL / MODEL / API_KEY", "local llama-server", "Deployment agent"],
        ["ECOMOPS_WEB_IMAGES", "on", "Enable web image search"],
    ], [2.2, 1.5, 2.6], font=7.2))
    s.append(Spacer(1, 12))
    s.append(p(
        "End of developer document. The editable draw.io sources, user guide and "
        "source code remain the authoritative references.", "DevSmall"))

    doc.build(s)
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    build()

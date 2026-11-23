"""Build the MSM User Guide PDF from screenshots and architecture diagrams."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
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
OUTPUT = ROOT / "docs" / "user-guide" / "MSM_User_Guide.pdf"

GOLD = colors.HexColor("#b08d4f")
INK = colors.HexColor("#1a1a1c")
MUTED = colors.HexColor("#5d5d63")
LIGHT = colors.HexColor("#f5f2eb")

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(
    "MSMCoverTitle", parent=styles["Title"], fontName="Helvetica-Bold",
    fontSize=34, leading=40, textColor=INK, spaceAfter=12,
))
styles.add(ParagraphStyle(
    "MSMCoverSub", parent=styles["Normal"], fontName="Helvetica",
    fontSize=15, leading=21, textColor=MUTED, spaceAfter=8,
))
styles.add(ParagraphStyle(
    "MSMH1", parent=styles["Heading1"], fontName="Helvetica-Bold",
    fontSize=20, leading=24, textColor=GOLD, spaceBefore=10, spaceAfter=10,
    keepWithNext=True,
))
styles.add(ParagraphStyle(
    "MSMH2", parent=styles["Heading2"], fontName="Helvetica-Bold",
    fontSize=13, leading=17, textColor=INK, spaceBefore=10, spaceAfter=5,
    keepWithNext=True,
))
styles.add(ParagraphStyle(
    "MSMBody", parent=styles["BodyText"], fontName="Helvetica",
    fontSize=9.5, leading=14, textColor=INK, spaceAfter=6,
))
styles.add(ParagraphStyle(
    "MSMBullet", parent=styles["MSMBody"], leftIndent=14, bulletIndent=4,
    spaceAfter=3,
))
styles.add(ParagraphStyle(
    "MSMCaption", parent=styles["Normal"], fontName="Helvetica-Oblique",
    fontSize=8, leading=11, textColor=MUTED, alignment=TA_CENTER, spaceAfter=10,
))
styles.add(ParagraphStyle(
    "MSMSmall", parent=styles["Normal"], fontName="Helvetica",
    fontSize=8.5, leading=12, textColor=MUTED,
))


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def para(text: str, style: str = "MSMBody"):
    return Paragraph(text, styles[style])


def bullets(items: list[str]):
    return [Paragraph(f"<bullet>&bull;</bullet> {esc(item)}", styles["MSMBullet"]) for item in items]


def shot(name: str, caption: str, max_width: float = 6.3, max_height: float = 4.4):
    path = SHOTS / name
    if not path.exists():
        return para(f"[missing screenshot: {esc(name)}]", "MSMSmall")
    img = Image(str(path))
    ratio = min(max_width * inch / img.imageWidth, max_height * inch / img.imageHeight, 1.0)
    img.drawWidth = img.imageWidth * ratio
    img.drawHeight = img.imageHeight * ratio
    return KeepTogether([img, para(caption, "MSMCaption")])


def diagram(name: str, caption: str):
    path = ARCH / name
    if not path.exists():
        return para(f"[missing diagram: {esc(name)}]", "MSMSmall")
    img = Image(str(path))
    ratio = min(6.3 * inch / img.imageWidth, 3.0 * inch / img.imageHeight, 1.0)
    img.drawWidth = img.imageWidth * ratio
    img.drawHeight = img.imageHeight * ratio
    return KeepTogether([img, para(caption, "MSMCaption")])


def table(data, widths, header=True):
    t = Table(data, colWidths=[w * inch for w in widths], repeatRows=1 if header else 0)
    style = [
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9c2b4")),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), GOLD),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
        ]
    t.setStyle(TableStyle(style))
    return t


def header_footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.8 * inch, 0.55 * inch, "MSM Operations Console - User Guide")
    canvas.drawRightString(A4[0] - 0.8 * inch, 0.55 * inch, f"Page {doc.page}")
    canvas.setStrokeColor(colors.HexColor("#ddd7cc"))
    canvas.line(0.8 * inch, 0.72 * inch, A4[0] - 0.8 * inch, 0.72 * inch)
    canvas.restoreState()


def build():
    doc = BaseDocTemplate(
        str(OUTPUT), pagesize=A4,
        leftMargin=0.8 * inch, rightMargin=0.8 * inch,
        topMargin=0.7 * inch, bottomMargin=0.8 * inch,
        title="MSM Operations Console - User Guide",
        author="MSM Deployment Agent",
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([PageTemplate(id="guide", frames=[frame], onPage=header_footer)])
    s: list = []

    # ---------------- Cover ----------------
    s.append(Spacer(1, 1.1 * inch))
    s.append(para("MSM Operations Console", "MSMCoverTitle"))
    s.append(para("Complete User Guide - Features, Workflows and Screenshots", "MSMCoverSub"))
    s.append(Spacer(1, 0.25 * inch))
    cover = table([
        ["Application", "MSM - event-driven commerce operations console"],
        ["Version", "0.4.0 (console skin v2, deployment agent)"],
        ["Access", "http://localhost:8501"],
        ["Demo login", "md@msm.local / supervisor@msm.local / accountant@msm.local / staff@msm.local"],
        ["Demo password", "msm"],
        ["Generated", "Automatically from the running application"],
    ], [1.5, 4.8], header=False)
    cover.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), LIGHT)]))
    s.append(cover)
    s.append(Spacer(1, 0.35 * inch))
    s.append(para(
        "This guide covers every workspace in the console, the theme system, the "
        "sidebar assistant and document intake, the chat model connector, the local "
        "git repository and the deployment Deep Agent. Screenshots were captured from "
        "the live application; architecture diagrams are included in Appendix A.",
        "MSMBody",
    ))
    s.append(NextPageTemplate("guide"))
    s.append(PageBreak())

    # ---------------- Contents ----------------
    s.append(para("Contents", "MSMH1"))
    s.extend(bullets([
        "1. Getting started - sign in, roles, navigation",
        "2. Dashboard - KPI rail and scenario actions",
        "3. Themes - six palettes, glass and flat, contrast",
        "4. Workspaces - all ten menus and how to use them",
        "5. Assistant, documents and chat model connector",
        "6. Configuration - LLM, grocery, IMAP and chat",
        "7. Deployment - git repo, Docker and the Deep Agent",
        "8. Security notes and troubleshooting",
        "Appendix A - Architecture diagrams",
        "Appendix B - Environment variables and file map",
    ]))
    s.append(PageBreak())

    # ---------------- 1 Getting started ----------------
    s.append(para("1. Getting started", "MSMH1"))
    s.append(para(
        "Open the application at <b>http://localhost:8501</b>. The sign-in page offers "
        "one demo account per role and shows the demo password. Choose an account from "
        "the dropdown, type the password and press SIGN IN.", "MSMBody"))
    s.append(shot("01-login.jpeg", "Figure 1 - Demo sign-in page"))
    s.append(para("1.1 Roles and permissions", "MSMH2"))
    s.append(table([
        ["Role", "Workspaces available", "Main ability"],
        ["MD", "All 10", "Full access including finance and approvals"],
        ["Supervisor", "9 (no finance)", "Operations, approvals and audit"],
        ["Staff", "7", "Day-to-day actions, no approvals or finance"],
        ["Accountant", "2", "Approvals and referrals/finance only"],
    ], [1.0, 1.6, 3.7]))
    s.append(Spacer(1, 6))
    s.append(para(
        "Role checks are enforced in three places: the navigation menu, the KPI rail and "
        "the actions inside each workspace. The active workspace persists across actions.", "MSMBody"))
    s.append(para("1.2 Navigation", "MSMH2"))
    s.extend(bullets([
        "The ten 3D-style icons in the top-right are the main menu. Hover an icon to see its name; the active icon is filled with its accent colour.",
        "The Theme pill sits immediately to the right of the icons.",
        "The sidebar keeps the Assistant toggle, Configuration, About and Sign out - it is no longer the primary menu.",
    ]))
    s.append(PageBreak())

    # ---------------- 2 Dashboard ----------------
    s.append(para("2. Dashboard (Overview)", "MSMH1"))
    s.append(shot("02-overview.jpeg", "Figure 2 - Overview workspace with KPI rail and scenario buttons"))
    s.append(para("2.1 KPI rail", "MSMH2"))
    s.append(para(
        "The header strip shows label and number only. Operations roles see Events, "
        "Pending, Reservations, Shipments, Discounts and Returns; finance roles also see "
        "Gross revenue, Refunds, Net revenue, Referral revenue, Commission due and "
        "Inventory value. Figures are computed directly from the event bus.", "MSMBody"))
    s.append(para("2.2 Scenario actions", "MSMH2"))
    s.extend(bullets([
        "RUN DEMO - simulates a customer request, intent capture, demand, clearance proposals and routing.",
        "CARRIER EXCEPTION - requires a routed shipment; reports a delay and runs the exception agent.",
        "SAMPLE HISTORY - seeds deterministic events for the Event trace workspace.",
    ]))
    s.append(para("2.3 Snapshot and charts", "MSMH2"))
    s.append(para(
        "The Snapshot shows units on hand, SKU count, routed shipments and awaiting "
        "approvals. Event flow and the cumulative timeline are derived from bus history.", "MSMBody"))
    s.append(PageBreak())

    # ---------------- 3 Themes ----------------
    s.append(para("3. Themes and appearance", "MSMH1"))
    s.append(shot("03-theme-glass.jpeg", "Figure 3 - Theme picker with a glass palette selected"))
    s.append(para(
        "The Theme pill opens a popover with six palettes, each carrying four distinct "
        "hues. Three use the frosted-glass material and three use a flat, solid-surface "
        "style. The GLASS / FLAT badge shows the active material. Soft and Crisp contrast "
        "levels are available at the bottom of the picker.", "MSMBody"))
    s.append(table([
        ["Style", "Themes"],
        ["Glass", "Obsidian Gold (default), Midnight Sapphire, Emerald Ledger"],
        ["Flat", "Terracotta Atelier, Rose Quartz, Graphite & Silver"],
    ], [1.1, 5.2]))
    s.append(Spacer(1, 8))
    s.append(shot("04-theme-flat.jpeg", "Figure 4 - A flat (non-glass) palette applied"))
    s.append(para(
        "Theme changes only re-skin the console: poster rendering and domain output keep "
        "their own branded colours.", "MSMBody"))
    s.append(PageBreak())

    # ---------------- 4 Workspaces ----------------
    s.append(para("4. Workspaces", "MSMH1"))

    s.append(para("4.1 Customer request", "MSMH2"))
    s.append(shot("05-customer-request.jpeg", "Figure 5 - Customer request form"))
    s.append(para(
        "Enter a customer id and free-text request, then press RUN PIPELINE. The "
        "Customer Requirements Agent extracts intent, line items, destination and "
        "urgency; matched items become demand signals. The last captured intent and any "
        "clarification question appear below the form.", "MSMBody"))

    s.append(para("4.2 Tasks", "MSMH2"))
    s.append(shot("06-tasks-cards.jpeg", "Figure 6 - Tasks dashboard with extracted items"))
    s.append(para(
        "Tasks are extracted from an IMAP inbox and from the customer form pipeline. "
        "READ INBOX pulls mail using the configured IMAP settings; Create tasks from a "
        "customer form accepts free text. Each card shows priority, source, due date, "
        "assignee and status. Use the status selector to move a task between open, in "
        "progress and done, and the Show filter to view a subset. Managers can enable "
        "All assignees.", "MSMBody"))
    s.append(shot("06-tasks-with-items.jpeg", "Figure 7 - Form submission creates task cards"))

    s.append(para("4.3 Stock clearance", "MSMH2"))
    s.append(shot("07-stock-clearance.jpeg", "Figure 8 - Stock clearance workspace"))
    s.extend(bullets([
        "Adjust Min age and Min stock, then ANALYSE WAREHOUSE STOCK to trigger the clearance agent.",
        "Open ADV GENERATOR to build a poster: choose an item, company name, old/new prices, up to five photos (upload or web images), tone and format.",
        "Generate a downloadable PNG poster and optionally push it to customers.",
    ]))

    s.append(para("4.4 Transit", "MSMH2"))
    s.append(shot("09-transit-with-data.jpeg", "Figure 9 - Routed shipments"))
    s.extend(bullets([
        "Shipments appear after dispatch proposals are approved.",
        "REPORT A DELIVERY EXCEPTION sends a carrier webhook through the Transit agent.",
        "CUSTOMER NOTICE builds a shipment poster plus message for one customer.",
    ]))
    s.append(shot("10-transit-notice.jpeg", "Figure 10 - Customer shipping notice generator"))

    s.append(para("4.5 Returns", "MSMH2"))
    s.append(shot("11-returns.jpeg", "Figure 11 - Returns workspace"))
    s.append(para(
        "Once a shipment has been delivered, the damaged-item flow creates a return "
        "assessment. Refunds and replacements are proposed here and wait for approval.", "MSMBody"))

    s.append(para("4.6 Approvals", "MSMH2"))
    s.append(shot("12-approvals.jpeg", "Figure 12 - Approval queue with risk badge"))
    s.append(para(
        "Proposals that exceed policy (for example a 30% discount against a 20% "
        "autonomous limit) appear here with a risk badge. GRANT ALL / DENY ALL or the "
        "per-proposal GRANT / DENY buttons resolve them. Refund proposals require "
        "finance approval permission.", "MSMBody"))

    s.append(para("4.7 Event trace", "MSMH2"))
    s.append(shot("13-event-trace.jpeg", "Figure 13 - Event trace with topic filter and search"))
    s.append(para(
        "Filter by topic or search by sku/customer/order id. Correlated events carry "
        "the same correlation id; causation ids show what triggered each event. The "
        "journal can be replayed with `ecomops replay --verify`.", "MSMBody"))

    s.append(para("4.8 Inventory", "MSMH2"))
    s.append(shot("14-inventory.jpeg", "Figure 14 - Inventory search and catalogue table"))
    s.append(para(
        "Search products, filter by category and toggle Grocery only. The bundled CSV "
        "adds 1,481 products next to the demo SKUs when grocery merge is enabled.", "MSMBody"))

    s.append(para("4.9 Referrals & finance", "MSMH2"))
    s.append(shot("15-finance.jpeg", "Figure 15 - Finance and referral read models"))
    s.append(para(
        "Shows gross revenue, refunds, net revenue, markdown value, inventory value and "
        "a deterministic referral ledger. Finance data is hidden from roles without "
        "view_finance permission.", "MSMBody"))
    s.append(PageBreak())

    # ---------------- 5 Assistant & connector ----------------
    s.append(para("5. Assistant, documents and chat connector", "MSMH1"))
    s.append(shot("16-assistant.jpeg", "Figure 16 - Sidebar assistant"))
    s.append(para(
        "Turn on the Assistant toggle in the sidebar. Ask questions in plain language "
        "(for example \"what is pending?\"). Deterministic intents answer first and "
        "enforce the role matrix; a configured local model can refine other questions. "
        "Attach a PDF, PNG or JPEG with the + control to run document intake: the app "
        "classifies the document, fills known fields, asks for gaps and produces a "
        "downloadable filled PDF.", "MSMBody"))
    s.append(para("5.1 Chat model connector", "MSMH2"))
    s.append(para(
        "The connector in <b>src/ecomops/chatmodel.py</b> supports three modes: "
        "<b>off</b> (rules only), <b>endpoint</b> (an OpenAI-compatible server such as "
        "Lemonade, Ollama or vLLM) and <b>gguf</b> (starts llama-server for a .gguf "
        "file on demand). The endpoint, model id, API key, GGUF path, port and context "
        "are configurable in the UI. A grounding guard rejects answers containing "
        "numbers that do not appear in the snapshot, falling back to rules.", "MSMBody"))
    s.append(PageBreak())

    # ---------------- 6 Configuration ----------------
    s.append(para("6. Configuration", "MSMH1"))
    s.append(shot("17-configuration.jpeg", "Figure 17 - Configuration panel in the sidebar"))
    s.extend(bullets([
        "LLM backend: Rule-based (deterministic) or MLX local model; the model repo id field is enabled for MLX.",
        "Merge grocery dataset: toggle the bundled CSV pack and set max products.",
        "Inbox (IMAP): host, port, username, password/app token, folder, message limit, TLS and mark-as-read. Credentials stay in the browser session.",
        "Chat model: mode, endpoint, model id, API key, GGUF path, port and context, with a Test chat model button.",
        "Reset system: clears document/UI state and rebuilds the system with clean history.",
    ]))
    s.append(PageBreak())

    # ---------------- 7 Deployment ----------------
    s.append(para("7. Deployment, git and the Deep Agent", "MSMH1"))
    s.append(para(
        "The application folder is a local git repository with an initial commit. "
        "Dockerfile, docker-compose.yml and .dockerignore build a Python 3.14 image "
        "with poppler for document intake and serve the console on port 8501.", "MSMBody"))
    s.append(table([
        ["Command", "Purpose"],
        ["git log --oneline", "Show local commits"],
        ["docker compose up -d --build", "Build and start the container"],
        ["curl localhost:8501/_stcore/health", "Health check"],
        [".venv/bin/python -m deploy_agent.cli \"...\"", "Run the deployment Deep Agent"],
    ], [3.2, 3.1]))
    s.append(Spacer(1, 8))
    s.append(para(
        "The deployment agent uses the LangChain Deep Agents SDK with the local GGUF "
        "model. Inspection and test tools are read-only. Mutating tools require the "
        "exact phrase DEPLOY-MSM in the request; no tool pushes to a remote.", "MSMBody"))
    s.append(PageBreak())

    # ---------------- 8 Security ----------------
    s.append(para("8. Security notes and troubleshooting", "MSMH1"))
    s.extend(bullets([
        "The default demo mode displays the password msm and grants full MD access. Do not expose this console to the internet; bind to 127.0.0.1 or place it behind real SSO and TLS.",
        "Set ECOMOPS_SESSION_SECRET to a long random value before exposing the optional FastAPI service.",
        "IMAP credentials are held in the browser session only and hidden in config reprs.",
        "If the assistant answers with rules only, check the chat model mode/endpoint or run Test chat model.",
        "If tasks are empty, configure IMAP or use the customer form to create tasks.",
        "If uploads fail, check poppler is installed for PDF intake and that files are under the allowed size/page limits.",
    ]))
    s.append(PageBreak())

    # ---------------- Appendix A ----------------
    s.append(para("Appendix A - Architecture diagrams", "MSMH1"))
    s.append(para(
        "The following diagrams are editable draw.io sources in docs/architecture/. "
        "Zoom in your PDF viewer for full detail.", "MSMBody"))
    s.append(diagram("msm-architecture.png", "Figure A1 - Complete application architecture"))
    s.append(Spacer(1, 12))
    s.append(diagram("msm-chat-connector.png", "Figure A2 - Chat connector detail"))
    s.append(PageBreak())

    # ---------------- Appendix B ----------------
    s.append(para("Appendix B - Environment variables and file map", "MSMH1"))
    s.append(para("Key environment variables", "MSMH2"))
    s.append(table([
        ["Variable", "Purpose"],
        ["ECOMOPS_SSO_MODE", "dev | oidc | proxy"],
        ["ECOMOPS_DEMO_PASSWORD", "Demo password (default msm)"],
        ["ECOMOPS_SESSION_SECRET", "HMAC secret for API bearer tokens"],
        ["ECOMOPS_MAIL_*", "IMAP host, port, user, password, folder, limit, SSL"],
        ["ECOMOPS_CHAT_MODE", "off | endpoint | gguf"],
        ["ECOMOPS_CHAT_BASE_URL / MODEL / API_KEY", "OpenAI-compatible chat endpoint"],
        ["ECOMOPS_CHAT_GGUF / PORT / CTX / NGL", "GGUF path and llama-server settings"],
        ["ECOMOPS_DEEPAGENTS_*", "Deployment agent model and API key"],
    ], [2.3, 4.0]))
    s.append(Spacer(1, 10))
    s.append(para("Important files", "MSMH2"))
    s.extend(bullets([
        "src/ecomops/app.py - Streamlit shell, navigation, workspaces",
        "src/ecomops/theme.py - six palettes, glass/flat tokens",
        "src/ecomops/system.py - composition root and entry points",
        "src/ecomops/events.py / bus.py - typed events and in-process bus",
        "src/ecomops/agents/ - five domain agents",
        "src/ecomops/mail.py - IMAP reader",
        "src/ecomops/chatmodel.py - chat model connector",
        "deploy_agent/ - deployment Deep Agent",
        "docs/architecture/ - editable draw.io diagrams",
    ]))
    s.append(Spacer(1, 14))
    s.append(para(
        "End of guide. For additional detail see README.md and the module docstrings "
        "in the source tree.", "MSMSmall"))

    doc.build(s)
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    build()

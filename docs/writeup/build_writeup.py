"""Render PROJECT_WRITEUP.md content into a PDF write-up."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, KeepTogether, PageBreak, PageTemplate,
    Paragraph, Spacer, Table, TableStyle,
)

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "docs" / "writeup" / "MSM_Project_Writeup.pdf"
DIAGRAM = ROOT / "docs" / "architecture" / "msm-architecture.png"
SHOT = ROOT / "docs" / "user-guide" / "screenshots" / "02-overview.jpeg"

GOLD = colors.HexColor("#b08d4f")
INK = colors.HexColor("#1a1a1c")
MUTED = colors.HexColor("#5d5d63")

ss = getSampleStyleSheet()
ss.add(ParagraphStyle("WTitle", parent=ss["Title"], fontName="Helvetica-Bold",
                      fontSize=30, leading=36, textColor=INK, spaceAfter=10))
ss.add(ParagraphStyle("WSub", parent=ss["Normal"], fontName="Helvetica",
                      fontSize=13, leading=19, textColor=MUTED, spaceAfter=8))
ss.add(ParagraphStyle("WH1", parent=ss["Heading1"], fontName="Helvetica-Bold",
                      fontSize=16, leading=20, textColor=GOLD, spaceBefore=10,
                      spaceAfter=7, keepWithNext=True))
ss.add(ParagraphStyle("WBody", parent=ss["BodyText"], fontName="Helvetica",
                      fontSize=9, leading=13, textColor=INK, spaceAfter=5))
ss.add(ParagraphStyle("WBullet", parent=ss["WBody"], leftIndent=14, bulletIndent=4,
                      spaceAfter=2))
ss.add(ParagraphStyle("WCap", parent=ss["Normal"], fontName="Helvetica-Oblique",
                      fontSize=8, leading=11, textColor=MUTED, spaceAfter=8))
ss.add(ParagraphStyle("WCode", parent=ss["Normal"], fontName="Courier",
                      fontSize=7.5, leading=10, backColor=colors.HexColor("#f2f0eb"),
                      borderPadding=5, spaceAfter=7))


def p(text, style="WBody"):
    return Paragraph(text, ss[style])


def bullets(items):
    return [Paragraph(f"<bullet>&bull;</bullet> {i}", ss["WBullet"]) for i in items]


def code(text):
    return Paragraph(text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>"), ss["WCode"])


def table(rows, widths):
    t = Table(rows, colWidths=[w * inch for w in widths], repeatRows=1)
    t.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 7.8),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9c2b4")),
        ("BACKGROUND", (0, 0), (-1, 0), GOLD),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def scaled_image(path, max_w=6.3, max_h=3.0):
    img = Image(str(path))
    ratio = min(max_w * inch / img.imageWidth, max_h * inch / img.imageHeight, 1.0)
    img.drawWidth, img.drawHeight = img.imageWidth * ratio, img.imageHeight * ratio
    return img


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.8 * inch, 0.55 * inch, "MSM - Project Write-up")
    canvas.drawRightString(A4[0] - 0.8 * inch, 0.55 * inch, f"Page {doc.page}")
    canvas.restoreState()


def build():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    doc = BaseDocTemplate(str(OUTPUT), pagesize=A4, leftMargin=0.8 * inch,
                          rightMargin=0.8 * inch, topMargin=0.7 * inch,
                          bottomMargin=0.8 * inch, title="MSM Project Write-up",
                          author="MSM Engineering")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="w")
    doc.addPageTemplates([PageTemplate(id="w", frames=[frame], onPage=footer)])
    s = []

    s.append(Spacer(1, 0.8 * inch))
    s.append(p("MSM Operations Console", "WTitle"))
    s.append(p("Project Write-up - Event-driven commerce operations at medium scale", "WSub"))
    s.append(Spacer(1, 0.15 * inch))
    s.append(p("<b>Repository:</b> github.com/Manohargov/EXIM-framework-for-medium-scale", "WBody"))
    s.append(p("<b>Status:</b> delivered, tested, documented and pushed", "WBody"))
    s.append(PageBreak())

    s.append(p("1. Executive summary", "WH1"))
    s.append(p(
        "MSM is a medium-scale, event-driven operations framework for e-commerce. It "
        "unifies customer requirements, stock clearance, transit management, "
        "returns/refunds, finance read models, task extraction and a role-based web "
        "console into one deterministic, offline-first system.", "WBody"))
    s.append(p(
        "The system demonstrates how several specialised agents can coordinate through "
        "a typed in-process event bus without a database or broker. An approval gate "
        "protects every side effect, deterministic rules guarantee offline operation, "
        "and AI is an optional enhancement rather than a dependency.", "WBody"))
    s.extend(bullets([
        "Ten permission-driven console workspaces",
        "Five domain agents plus a task-extraction agent",
        "IMAP and customer-form task pipelines",
        "Document intake and filled PDF output",
        "Pillow poster and transit notice rendering",
        "Six four-hue themes: 3 glass, 3 flat",
        "Chat connector: off / endpoint / GGUF",
        "LangChain Deep Agents deployment automation",
        "293 tests and full user/developer documentation",
    ]))
    s.append(PageBreak())

    s.append(p("2. Problem and objectives", "WH1"))
    s.append(p(
        "Medium-scale commerce operations typically span disconnected tools: email "
        "inboxes, spreadsheets, warehouse exports, carrier portals and finance reports. "
        "Important work is lost between systems, approvals are informal, and there is no "
        "shared audit trail. Adding an LLM to such a system often makes it less reliable.", "WBody"))
    s.extend(bullets([
        "One coordination model: every business fact is a typed event with correlation and causation.",
        "Deterministic by default: rules cover every LLM task; AI degrades safely.",
        "Human control: risky or high-value actions require approval.",
        "Role-aware access: MD, Supervisor, Staff and Accountant see only their entitled workspaces.",
        "Operational visibility: dashboards, KPIs and metrics are derived from event history.",
        "Extensibility: add events, rules, agents, channels and themes without rewriting the core.",
    ]))
    s.append(PageBreak())

    s.append(p("3. Solution overview", "WH1"))
    s.append(scaled_image(DIAGRAM, max_h=2.9))
    s.append(p("Figure 1 - Complete system architecture", "WCap"))
    s.append(code(
        "UI (Streamlit) --+\n"
        "CLI -------------+--> EcomopsSystem --> EventBus --> 5 agents\n"
        "API (FastAPI) ---+        |                |\n"
        "                          +-- ApprovalGate   +-- Journal / Metrics\n"
        "                          +-- LLM + Rules    +-- Inventory / Finance\n"
        "                          +-- Embedder       +-- Documents / Renderers"))
    s.append(p(
        "All front ends share the same composition root. Agents subscribe to events, "
        "ask the LLM interface for structured JSON, validate it with Pydantic and "
        "publish new events. Side effects follow ActionProposed -> ApprovalRequested -> "
        "ActionApproved/Rejected -> act().", "WBody"))
    s.append(PageBreak())

    s.append(p("4. Key capabilities", "WH1"))
    s.append(table([
        ["Flow", "Outcome"],
        ["Customer request", "Intent, SKU matching, demand, clarification when ambiguous"],
        ["Stock clearance", "Overstock detection, discount proposals, approval-gated discounts"],
        ["Transit", "Routing, shipments, carrier exceptions, resolution"],
        ["Returns", "Damaged-item assessment, refund/replacement proposals"],
        ["Finance", "Revenue, refunds, markdown, referral ledger"],
        ["Tasks", "IMAP/form extraction into a per-user task board with status changes"],
    ], [1.6, 4.7]))
    s.append(Spacer(1, 8))
    s.append(p(
        "The console adds a 3D icon navigation rail, compact KPI strip, six four-hue "
        "themes, role-gated workspaces, a role-aware assistant and an escaped-HTML "
        "rendering layer.", "WBody"))
    s.append(scaled_image(SHOT, max_h=3.6))
    s.append(p("Figure 2 - Operations console", "WCap"))
    s.append(PageBreak())

    s.append(p("5. Technology and decisions", "WH1"))
    s.append(table([
        ["Decision", "Rationale"],
        ["Event bus instead of broker/DB", "Offline, deterministic, easy to test and replay"],
        ["Pydantic v2", "Validation and schema export to LLM prompts"],
        ["Rule-first LLM", "Predictable offline behaviour, safe fallback"],
        ["Approval gate", "Auditability and human control over side effects"],
        ["CSS-variable themes", "Skin changes without widget/domain rebuilds"],
        ["Streamlit console", "Fast multi-workspace operator UI"],
        ["FastAPI control plane", "Separate automation/integration surface"],
        ["Deep Agents SDK", "Planning and tool harness for deployment automation"],
    ], [2.1, 4.2]))
    s.append(PageBreak())

    s.append(p("6. Data and storage", "WH1"))
    s.append(p(
        "There is no relational database. The bus is the source of truth; the journal "
        "is an append-only audit log; session state holds UI and connection settings; "
        "the catalogue is a CSV; images and document workdirs are files. Task state is "
        "a pure fold of TaskExtracted and TaskStatusChanged.", "WBody"))
    s.append(table([
        ["Store", "Contents"],
        ["EventBus", "Typed events with correlation/causation"],
        ["EventJournal", "Append-only JSONL audit trail"],
        ["session_state", "User, view, theme, chat, document and connection config"],
        ["Inventory / CSV", "Built-in SKUs plus 1,481 grocery products"],
        ["Files", "Image cache, 0700 document workdirs, generated posters"],
    ], [1.5, 4.8]))
    s.append(PageBreak())

    s.append(p("7. Delivery and verification", "WH1"))
    s.extend(bullets([
        "ruff check src tests deploy_agent - clean",
        "pytest -q - 293 passed",
        "Streamlit health endpoint - ok",
        "Browser smoke tests - workspaces, themes, tasks, approvals verified",
        "GitHub device login - no token stored in source",
        "Pushed to Manohargov/EXIM-framework-for-medium-scale, branch master",
    ]))
    s.append(p("8. Security posture", "WH1"))
    s.extend(bullets([
        "Demo password msm is for local use only; use OIDC/proxy and TLS in production.",
        "Set ECOMOPS_SESSION_SECRET before exposing the API.",
        "Secrets stay in session/env and are hidden in reprs and events.",
        "Uploads are limited; filenames sanitized; subprocess uses argument lists.",
        "Deployment agent requires DEPLOY-MSM for mutations and never pushes.",
    ]))
    s.append(p("9. Future enhancements", "WH1"))
    s.extend(bullets([
        "Add CI workflow with a workflow-scoped token.",
        "Add persistent event backlog for long-running deployments.",
        "Provide OIDC/reverse-proxy templates.",
        "Evaluate tool-calling quality for the local chat model.",
        "Add document ownership checks to the HTTP API.",
        "Add rate limiting and request-size limits.",
    ]))
    s.append(p(
        "<b>Conclusion.</b> MSM shows that a multi-agent operations system can be "
        "reliable, explainable and useful without a cloud LLM. The event bus gives a "
        "complete audit trail, rules keep the system functional offline, the approval "
        "gate keeps humans in control, and optional model paths add intelligence where "
        "it helps.", "WBody"))

    doc.build(s)
    print(f"wrote {OUTPUT}")


if __name__ == "__main__":
    build()

# EXIM Framework — MSM Operations Console

<div align="center">

![MSM Console](docs/user-guide/screenshots/02-overview.jpeg)

**Event-driven commerce operations, executed with precision.**

[![Python](https://img.shields.io/badge/python-3.14-3776ab.svg)](https://www.python.org/)
[![Streamlit](https://img.shields.io/badge/streamlit-1.67-ff4b4b.svg)](https://streamlit.io/)
[![FastAPI](https://img.shields.io/badge/FastAPI-optional-009688.svg)](#cli-and-http-api)
[![Tests](https://img.shields.io/badge/tests-293%20passed-2ea44f.svg)](#testing)
[![Lint](https://img.shields.io/badge/ruff-clean-2ea44f.svg)](#testing)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](#license)
[![Docs](https://img.shields.io/badge/docs-3%20PDFs-orange.svg)](#documentation)
[![Release](https://img.shields.io/github/last-commit/Manohargov/EXIM-framework-for-medium-scale?color=blue)](https://github.com/Manohargov/EXIM-framework-for-medium-scale)

[Quick start](#quick-start) · [Architecture](#architecture) · [Documentation](#documentation) · [Repository](https://github.com/Manohargov/EXIM-framework-for-medium-scale)

</div>

## Table of contents

- [What this is](#what-this-is)
- [Feature highlights](#feature-highlights)
- [Quick start](#quick-start)
- [Application tour](#application-tour)
- [Architecture](#architecture)
  - [Full system diagram (draw.io)](#full-system-diagram-drawio)
  - [Event lifecycle](#event-lifecycle)
  - [Diagram sources](#diagram-sources)
- [Repository layout](#repository-layout)
- [Data and storage model](#data-and-storage-model)
- [AI / ML integration](#ai--ml-integration)
- [Tasks from mail and forms](#tasks-from-mail-and-forms)
- [Theme system](#theme-system)
- [Document intake](#document-intake)
- [Posters and promotions](#posters-and-promotions)
- [CLI and HTTP API](#cli-and-http-api)
- [Deployment](#deployment)
- [Documentation](#documentation)
- [Configuration reference](#configuration-reference)
- [Testing](#testing)
- [Security notes](#security-notes)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## What this is

MSM is a medium-scale operations framework for e-commerce: customer requirements,
stock clearance, transit management, returns/refunds, finance read models, a role-based
console, and a deterministic rule engine with optional local AI models.

Everything is coordinated through a typed in-process event bus. Agents never call
each other directly; side effects pass through an approval gate. The console, CLI and
optional FastAPI service all share the same `EcomopsSystem` composition root.

| Guarantee | How it is achieved |
|---|---|
| **No external database** | The event bus plus an optional JSONL journal are the source of truth |
| **Offline by default** | Deterministic rules cover every LLM task; AI is opt-in and degrades safely |
| **Multi-role** | MD, Supervisor, Staff and Accountant see different workspaces and actions |
| **Human control** | Every side effect starts as `ActionProposed` and waits for approval |
| **Runnable anywhere** | Local Streamlit server, Docker container, or FastAPI service |
| **Extensible** | Add an event, rule, agent, channel or theme without touching the domain core |

---

## Feature highlights

| Area | Capability |
|---|---|
| **Operations** | Customer request → intent → demand → clearance → shipment → exception → return flows |
| **Dashboards** | Ten permission-driven workspaces, compact KPI rail, event trace and charts |
| **Tasks** | Extract actionable items from IMAP mail and customer forms into a per-user task board |
| **Documents** | Upload PDF/PNG/JPEG; classify, extract fields, resolve, fill and download |
| **Creatives** | Clearance posters and transit notices rendered with Pillow; web image search |
| **Themes** | Six four-hue palettes: 3 frosted-glass and 3 flat; Soft/Crisp contrast |
| **Assistant** | Role-aware Q&A with deterministic fallback and optional local LLM |
| **AI connector** | Off / OpenAI-compatible endpoint / GGUF via llama-server, with number grounding |
| **Integration** | CLI, FastAPI control plane, JSONL journal, Prometheus metrics |
| **Deployment** | Dockerfile, Compose, local git repo and a LangChain Deep Agents deploy agent |

---

## Quick start

### 1. Local console (recommended)

The repository ships a ready-to-run `.venv` with rewritten paths.

```bash
cd msm
.venv/bin/python -m streamlit run src/ecomops/app.py \
  --server.port 8501 --server.headless true --server.address 127.0.0.1
```

Open <http://localhost:8501>.

**Demo accounts** (password `msm`):

| Account | Role | Access |
|---|---|---|
| `md@msm.local` | Managing Director | Everything |
| `supervisor@msm.local` | Supervisor | Operations, approvals, audit |
| `staff@msm.local` | Staff | Day-to-day actions |
| `accountant@msm.local` | Accountant | Finance and refunds only |

### 2. Recreate the environment

```bash
cd msm
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python -e ".[dev,ui,api,chat,docs]"
```

### 3. Verify

```bash
.venv/bin/ruff check src tests deploy_agent
.venv/bin/python -m pytest -q
curl http://localhost:8501/_stcore/health   # -> ok
```

---

## Application tour

![Workspace screenshot](docs/user-guide/screenshots/12-approvals.jpeg)

The top-right icon rail is the primary navigation. Hover an icon to see its name; the
active icon is filled with the current theme accent. The Theme pill sits beside it.
The sidebar holds the Assistant, Configuration, About and Sign out.

| Workspace | What it does |
|---|---|
| **Overview** | KPI rail, Run Demo, Carrier Exception, Sample History, snapshot and event charts |
| **Customer request** | Submit free-text requests, inspect captured intent and clarifications |
| **Stock clearance** | Analyse overstock, review discount strategies, generate clearance posters |
| **Transit** | Shipments, carrier exceptions, customer shipping notices |
| **Returns** | Damaged-item flow, refund/replacement assessments |
| **Approvals** | Risk-scoped proposal queue; grant/deny individually or in bulk |
| **Event trace** | Filter/search the event trail with correlation and causation ids |
| **Inventory** | Search/filter catalogue; optionally merge the 1,481-product grocery CSV |
| **Referrals & finance** | Revenue, refunds, markdown value and a deterministic referral ledger |
| **Tasks** | Email/form-derived task board with status transitions and per-user scoping |

A full walkthrough with screenshots is in [`docs/user-guide/MSM_User_Guide.pdf`](docs/user-guide/MSM_User_Guide.pdf).

---

## Architecture

This diagram is functional: every edge says **what changes the data** (free text →
typed events → proposals → approvals → folded read models → dashboards, PDFs and
posters), and each numbered stage names the **module that owns it**, so you always
know which file to open. The approval stage is shown as its own gate because that is
where the system refuses to run unattended: no side effect reaches the bus without a
human decision.

```mermaid
%%{init: {"theme":"base","themeVariables":{"fontFamily":"ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif","fontSize":"13px","primaryColor":"#eef2ff","primaryTextColor":"#0f172a","primaryBorderColor":"#818cf8","lineColor":"#94a3b8","clusterBkg":"#f8fafc","clusterBorder":"#e2e8f0","edgeLabelBackground":"#ffffff","titleColor":"#334155"},"flowchart":{"curve":"basis","nodeSpacing":30,"rankSpacing":40,"padding":8,"useMaxWidth":true}}}%%
flowchart TB

    subgraph ENTRY["1 · ENTRY — four surfaces, one composition root"]
        direction LR
        UI["🖥️ Streamlit console<br/><i>app.py · icon nav · KPI rail · six themes</i>"]
        CLI["⌨️ CLI<br/><i>cli.py · demo / request / replay</i>"]
        API["🔌 FastAPI<br/><i>api.py · bearer tokens · role guards</i>"]
        DAG["🤖 Deploy Deep Agent<br/><i>deploy_agent/ · DEPLOY-MSM gate</i>"]
    end

    subgraph CORE["2 · CORE — system.py · the only place everything is wired"]
        SYS["EcomopsSystem<br/><b>bus · ApprovalGate · LLM+rules · embedder · inventory</b>"]
    end

    subgraph BUSG["3 · BUS — bus.py · typed events are the source of truth"]
        direction TB
        BUS["EventBus<br/><b>data: typed events + correlation_id + causation_id</b><br/>drain() runs subscribers in registration order"]
        JOURNAL["EventJournal<br/><b>data: append-only JSONL audit trail</b>"]
        METRICS["Metrics<br/><b>data: counters → Prometheus text</b>"]
    end

    subgraph DOMAIN["4 · DOMAIN — agents/ + rules.py · agents only exchange events"]
        direction TB
        AGENTS["5 agents<br/><b>event → snapshot → prompt → JSON → Pydantic → event</b><br/>customer · clearance · transit · returns · tasks"]
        RULES["8 deterministic rules<br/><b>capture_intent · extract_tasks · plan_route · assess_return · …</b><br/>RuleBasedLLM always answers offline"]
        MODEL["optional local model<br/><i>MLX Qwen · chat connector: off / endpoint / gguf</i>"]
        AGENTS -.->|"ask"| RULES
        AGENTS -.->|"try first"| MODEL
        MODEL -.->|"any failure → rules"| RULES
    end

    subgraph GATE["5 · APPROVAL — policy.py · no side effect without a human"]
        direction TB
        PROPOSE["ActionProposed<br/><b>data: risk scope + proposed effect</b>"]
        REQUEST["ApprovalRequested → Approvals workspace"]
        DECIDE{"decision"}
        ACT["act()<br/><b>data: effect → new domain events</b>"]
        REJECT["ActionRejected<br/><i>recorded, nothing executed</i>"]
        PROPOSE --> REQUEST --> DECIDE
        DECIDE -->|"approved"| ACT
        DECIDE -->|"rejected"| REJECT
    end

    subgraph READ["6 · READ MODELS — pure folds, never mutated in place"]
        direction LR
        FIN["Finance<br/><b>revenue · refunds · markdown · referrals</b>"]
        TASKS["Task board<br/><b>fold of TaskExtracted + TaskStatusChanged</b>"]
        DASH["KPI rail · Event trace<br/><b>fold of everything before it</b>"]
    end

    subgraph OUT["7 · OUTPUT — documents/ · advert.py · promotion.py"]
        direction LR
        DOC["Document intake<br/><b>bytes → fields → filled PDF</b>"]
        POST["Posters and notices<br/><b>Pillow · brand colours, theme-independent</b>"]
        CHAN["Channels<br/><b>dispatch → PromotionPublished / TransitNoticeSent</b>"]
    end

    MAIL["📮 IMAP inbox and customer form<br/><i>mail.py · MIME parse</i>"]
    REL["8 · RELEASE — local git → git push → GitHub<br/><b>README · 3 PDFs · draw.io diagrams · MIT · About</b>"]

    UI -->|"submit / approve"| SYS
    CLI -->|"request / demo"| SYS
    API -->|"automation"| SYS
    DAG -->|"run_tests / health_check"| SYS
    SYS --> BUS
    MAIL -->|"EmailReceived / FormReceived"| BUS
    BUS -->|"drain → subscribe"| AGENTS
    AGENTS -->|"risky side effect"| PROPOSE
    ACT -->|"domain events"| BUS
    REJECT -.->|"audit"| JOURNAL
    BUS -.-> JOURNAL
    BUS -.-> METRICS
    BUS -->|"history"| READ
    BUS -->|"resolve fields against"| DOC
    AGENTS -->|"copy / layout"| POST
    POST --> CHAN
    DOC -.->|"DocumentFilled"| BUS
    CHAN -.->|"Published / Sent"| BUS
    READ -->|"rendered"| UI
    DAG -.->|"commit (never push)"| REL
    REL -.->|"clone / read docs"| UI

    classDef entry fill:#eef2ff,stroke:#818cf8,color:#0f172a
    classDef core fill:#f5f3ff,stroke:#a78bfa,color:#0f172a
    classDef bus fill:#e0f2fe,stroke:#38bdf8,color:#0f172a
    classDef domain fill:#ccfbf1,stroke:#2dd4bf,color:#0f172a
    classDef gate fill:#fef3c7,stroke:#f59e0b,color:#0f172a
    classDef fold fill:#dcfce7,stroke:#4ade80,color:#0f172a
    classDef out fill:#fce7f3,stroke:#f472b6,color:#0f172a
    class UI,CLI,API,DAG entry
    class SYS core
    class BUS,JOURNAL,METRICS bus
    class AGENTS,RULES,MODEL domain
    class PROPOSE,REQUEST,DECIDE,ACT,REJECT gate
    class FIN,TASKS,DASH fold
    class DOC,POST,CHAN,MAIL,REL out
```

Points worth knowing, because each one exists for a reason that is easy to
re-break:

- **Agents never call each other.** They only exchange typed events; an agent
  that needs something publishes an event and lets a subscriber react, so any
  flow can be replayed from the journal alone.
- **`drain()` is deterministic.** Subscribers run in registration order on one
  thread — no races, and `replay --verify` reproduces the same read models.
- **Rules are the floor, not a fallback.** Every LLM task has a rule handler, so
  the console demos with no model installed; the model may only *beat* the rule,
  never be required for the app to start.
- **Approval happens before the effect.** `act()` is unreachable without
  `ActionApproved`; a rejection is recorded and executes nothing.
- **Read models are folds.** Finance, tasks and the KPI rail are derived by
  reducing events — mutating them in place would break replay.
- **The theme is a skin.** `theme.py` swaps CSS variables only; poster and
  document colours are brand constants, so a palette change can never alter a
  printed price.
- **The deploy agent commits, never pushes.** Its mutating tools need the exact
  phrase `DEPLOY-MSM`, and no tool in `deploy_agent/` has a remote-push
  implementation.

### Full system diagram (draw.io)

![Complete architecture — editable draw.io source in docs/architecture](docs/architecture/msm-architecture.png)

*The same system as a drawn layout — entry points, coordination core, domain agents,
storage and the release pipeline to GitHub. Exported from the editable draw.io file
described in [Diagram sources](#diagram-sources) — **click the image for the
full-resolution view**.*

### Event lifecycle

A typical order for a risky side effect:

```text
UI / CLI / API
    │  submit request
    ▼
ActionProposed ──► ApprovalRequested ──► Approvals workspace
                                            │
                        ┌───────────────────┴───────────────────┐
                        ▼                                       ▼
                 ActionApproved                            ActionRejected
                        │                                       │
                        ▼                                       ▼
                    act() ──► domain events ──► bus ──► read models
                                                          │
                                          ┌───────────────┼───────────────┐
                                          ▼               ▼               ▼
                                     KPI rail        Event trace      Finance ledger
```

Every event carries `correlation_id` and `causation_id`, so the Event trace workspace
can reconstruct the full chain — request → proposal → approval → effect — without a
relational database.

### Diagram sources

All diagrams are authored in [draw.io / diagrams.net](https://app.diagrams.net/) and
committed as editable `.drawio` files next to their PNG exports:

| Diagram | Source | Export |
|---|---|---|
| Full system architecture | [`msm-architecture.drawio`](docs/architecture/msm-architecture.drawio) | [`msm-architecture.png`](docs/architecture/msm-architecture.png) |
| Chat model connector | [`msm-chat-connector.drawio`](docs/architecture/msm-chat-connector.drawio) | [`msm-chat-connector.png`](docs/architecture/msm-chat-connector.png) |
| Functional / structural views | [`ecomops-architecture.drawio`](docs/architecture/ecomops-architecture.drawio) | [`ecomops-architecture.pdf`](docs/architecture/ecomops-architecture.pdf) |

`msm-architecture-graph.json` is the deterministic graph source (nodes + edges) used to
regenerate the main diagram:

```bash
cd docs/architecture
# lay out nodes/edges into an editable .drawio (drawio-skill autolayout script)
python3 autolayout.py msm-architecture-graph.json -o msm-architecture.drawio
# export a 2x PNG for the README and PDFs
drawio -x -f png -e -s 2 -o msm-architecture.png msm-architecture.drawio
```

Open any `.drawio` file in draw.io desktop or <https://app.diagrams.net/> to edit;
the PNG above and the figures inside the PDFs are re-exported from these sources.

Developer details: [`docs/developer-doc/MSM_Developer_Document.pdf`](docs/developer-doc/MSM_Developer_Document.pdf).

---

## Repository layout

```text
msm/
├── src/ecomops/
│   ├── app.py              Streamlit console, navigation, workspaces
│   ├── theme.py            Six palettes, glass/flat CSS variables
│   ├── system.py           Composition root and entry points
│   ├── events.py / bus.py  Typed events, correlation/causation, history
│   ├── policy.py           Approval gate and policy thresholds
│   ├── llm.py / rules.py   LLM protocol, rule fallback, embedder
│   ├── agents/             Customer, clearance, transit, returns, tasks
│   ├── inventory.py        Catalogue, stock, matching
│   ├── finance.py          Revenue/refund/referral read models
│   ├── metrics.py          Event counters and Prometheus text
│   ├── mail.py             IMAP reader and MIME parsing
│   ├── tasks.py            Task fold and status transitions
│   ├── chat.py / chatmodel.py  Assistant and model connector
│   ├── documents/          Extract, classify, resolve, fill
│   ├── advert.py           Poster and notice rendering
│   ├── webimages.py        Web image search and cache
│   ├── promotion.py        Dispatch channels and reports
│   └── api.py / cli.py     HTTP control plane and CLI
├── deploy_agent/           LangChain Deep Agents deployment automation
├── data/                   Grocery catalogue and image cache
├── docs/                   draw.io diagrams, user guide, developer doc, write-up
├── tests/                  293 offline, deterministic tests
├── Dockerfile / docker-compose.yml / .dockerignore
├── AGENTS.md               Agent operating rules for this repo
├── LICENSE                 MIT
├── PROJECT_WRITEUP.md      Executive project write-up
└── README.md               This document
```

---

## Data and storage model

There is **no relational database**. The system uses:

| Store | Technology | Contents |
|---|---|---|
| Event bus | In-memory list | Typed events with correlation/causation |
| Journal | JSONL file | Durable append-only audit trail |
| Session state | Streamlit server-side | User, active view, theme, chat history, document/IMAP/chat config |
| Inventory | In-memory dict | Built-in SKUs plus optional grocery CSV |
| Catalogue | CSV | 1,481 grocery products |
| Image cache | Disk | Downloaded web images |
| Document workdirs | `tempfile.mkdtemp()` | Uploaded and filled files (0700) |
| Task state | Derived | Fold of `TaskExtracted` + `TaskStatusChanged` |

Core schemas are Pydantic v2 models. `Event` enforces `extra="forbid"`; `event_classes()`
auto-registers new event types for the journal and API. See the developer document for
every field.

---

## AI / ML integration

### Deterministic first

`rules.py` registers eight tasks used by agents and UI:

```text
capture_intent          extract_tasks
develop_clearance_strategy   plan_route
resolve_exception       assess_return
write_advert_copy       write_transit_copy
```

`RuleBasedLLM` always works offline. `FallbackLLM` tries an optional MLX model
(`mlx-community/Qwen3-4B-4bit`) and falls back to rules on any failure. Embeddings use
`all-MiniLM-L6-v2` when `sentence-transformers` is installed, otherwise token overlap.

### Chat connector

![Chat connector — editable draw.io source in docs/architecture](docs/architecture/msm-chat-connector.png)

`src/ecomops/chatmodel.py` supports three modes:

| Mode | Behaviour |
|---|---|
| `off` | No model call; deterministic answers only |
| `endpoint` | Any OpenAI-compatible server; model id auto-discovered from `/models` |
| `gguf` | Starts `llama-server` for a `.gguf` path, stops only its own process |

The assistant builds a compact JSON snapshot, sends it with the question, and rejects
answers containing numbers absent from the snapshot — preventing invented figures.

Configure in **Configuration → Chat model** or with environment variables. Press
**Test chat model** to verify the endpoint before use.

---

## Tasks from mail and forms

The **Tasks** workspace is a per-user dashboard. Tasks come from:

1. **IMAP inbox** — configure host, port, user and password/app token in the sidebar, then press **Read inbox** in Tasks. TLS is default; scanning is read-only unless **Mark as read** is enabled.
2. **Customer form** — *Create tasks from a customer form* feeds free text through the same extractor without an inbox.

Both sources publish `EmailReceived` or `FormReceived`. The `TaskExtractionAgent` uses the
deterministic `extract_tasks` rule (or optional model) and emits `TaskExtracted` events.
Status changes append `TaskStatusChanged`; the dashboard folds current state. Managers
can enable **All assignees**.

---

## Theme system

`theme.py` defines six palettes, each with four distinct hues:

| Material | Themes |
|---|---|
| **Glass** | Obsidian Gold (default), Midnight Sapphire, Emerald Ledger |
| **Flat** | Terracotta Atelier, Rose Quartz, Graphite & Silver |

Each theme exposes surface, text, accent and glass tokens as CSS variables injected after
the base stylesheet. Soft and Crisp contrast levels are available in the picker. Switching
themes never rebuilds widgets; posters and domain output keep their own branded palettes.

![Glass theme](docs/user-guide/screenshots/03-theme-glass.jpeg)
![Flat theme](docs/user-guide/screenshots/04-theme-flat.jpeg)

---

## Document intake

Attach a PDF, PNG or JPEG in the assistant composer:

1. Validate type, size (10 MB) and page count (10).
2. Extract text and word boxes (`pdftohtml` / `pdftoppm` + optional Tesseract OCR).
3. Classify as purchase order, return slip, stock sheet or generic form.
4. Extract fields from regex specs and resolve them against inventory and bus events.
5. Ask the operator only for blocking gaps.
6. Fill the original layout or produce a reflowed PDF, then offer download.

The audit trail records `DocumentUploaded → DocumentFieldsResolved → DocumentFilled`
with metadata only — never document bytes.

---

## Posters and promotions

- **Clearance poster**: choose from applied discounts, proposals or stock; set company, old/new prices, up to five photos (upload or web), tone and format.
- **Transit notice**: pick a routed shipment, confirm details, render a poster and send it to that customer through a chosen channel.
- Rendering is Pillow-based with fixed branded colours, independent of the console theme.
- Dispatch results are published as `PromotionPublished` / `TransitNoticeSent` events.

---

## CLI and HTTP API

```bash
# CLI
.venv/bin/ecomops demo --approve yes
.venv/bin/ecomops request "3 mechanical keyboards to Berlin ASAP"
.venv/bin/ecomops exceptions
.venv/bin/ecomops stock
.venv/bin/ecomops returns
.venv/bin/ecomops catalogue --grocery data/grocery_catalogue.csv --search "peanut butter"
.venv/bin/ecomops replay events.jsonl --verify
.venv/bin/ecomops ui

# API
.venv/bin/ecomops serve --port 8000
curl http://localhost:8000/docs
```

The API uses `SessionCodec` bearer tokens, role guards, and endpoints for requests,
stock, exceptions, returns, approvals, documents, events, finance and metrics. Every
command accepts `--journal PATH` for durable event replay.

---

## Deployment

### Docker

```bash
docker compose up -d --build
curl http://localhost:8501/_stcore/health
```

The image uses Python 3.14 slim, installs poppler for document intake, and serves the
console on port 8501 with a healthcheck.

### Deep Agents deployment agent

```bash
export ECOMOPS_DEEPAGENTS_BASE_URL=http://127.0.0.1:38285/v1
export ECOMOPS_DEEPAGENTS_MODEL=MiMo-V2.6-Distill-Qwen-9B-Q8_0
export ECOMOPS_DEEPAGENTS_API_KEY=<local-llama-server-key>

.venv/bin/python -m deploy_agent.cli "check repo status and run the tests"
.venv/bin/python -m deploy_agent.cli "build the Docker image DEPLOY-MSM"
```

The agent uses the LangChain Deep Agents SDK with the local GGUF runtime. Read-only
tools are free; mutating tools require the exact phrase `DEPLOY-MSM` in the request.
No tool pushes to a remote. See [`deploy_agent/README.md`](deploy_agent/README.md).

---

## Documentation

| Document | Contents |
|---|---|
| [Project write-up](PROJECT_WRITEUP.md) / [PDF](docs/writeup/MSM_Project_Writeup.pdf) | Executive summary, problem, solution, decisions, verification and future work |
| [User guide](docs/user-guide/MSM_User_Guide.pdf) | 21 pages, all features, workflows and screenshots |
| [Developer document](docs/developer-doc/MSM_Developer_Document.pdf) | 16 pages: architecture, schemas, storage, UI/UX, AI, flows, security |
| [Architecture diagrams](docs/architecture/) | Editable `.drawio` sources and PNG/PDF exports |
| [Document intake plan](docs/document-intake-plan.md) | Intake pipeline design and limits |
| [This README](README.md) | Setup and navigation |

---

## Configuration reference

| Variable | Default | Purpose |
|---|---|---|
| `ECOMOPS_SSO_MODE` | `dev` | `dev`, `oidc` or `proxy` authentication |
| `ECOMOPS_DEMO_PASSWORD` | `msm` | Demo credential |
| `ECOMOPS_SESSION_SECRET` | dev placeholder | HMAC key for API bearer tokens |
| `ECOMOPS_AUTH_AUTOLOGIN` | unset | Test-only role bypass |
| `ECOMOPS_GROCERY` | auto | CSV path or `none` to disable |
| `ECOMOPS_MAIL_HOST` | unset | IMAP host |
| `ECOMOPS_MAIL_PORT` | `993` | IMAP port |
| `ECOMOPS_MAIL_USER` / `ECOMOPS_MAIL_PASSWORD` | unset | IMAP credentials |
| `ECOMOPS_MAIL_FOLDER` / `LIMIT` / `SSL` / `MARK_SEEN` | `INBOX` / `20` / `true` / `false` | Inbox behaviour |
| `ECOMOPS_CHAT_MODE` | `endpoint` | `off`, `endpoint` or `gguf` |
| `ECOMOPS_CHAT_BASE_URL` | Lemonade `13305` | Chat endpoint |
| `ECOMOPS_CHAT_MODEL` | `Llama-3.2-1B-Instruct-GGUF` | Model id |
| `ECOMOPS_CHAT_GGUF` | unset | GGUF file path |
| `ECOMOPS_CHAT_PORT` / `CTX` / `NGL` | `38285` / `8192` / `-1` | llama-server settings |
| `ECOMOPS_DEEPAGENTS_*` | local llama-server | Deployment agent model/key |
| `ECOMOPS_WEB_IMAGES` | `on` | Enable web image search |

---

## Testing

```bash
.venv/bin/ruff check src tests deploy_agent
.venv/bin/python -m pytest -q
```

- **293 tests**, fully offline and deterministic.
- Coverage includes UI flows, role gates, themes, event bus, rules, agents, documents,
  posters, notices, mail, task extraction, chat connector and deployment tools.

---

## Security notes

- The demo mode prints the password `msm` and grants full MD access. **Do not expose it
  to the internet**; bind to `127.0.0.1` or configure OIDC/proxy with TLS.
- Set `ECOMOPS_SESSION_SECRET` to a long random value before exposing the API.
- IMAP and chat secrets stay in the browser session or environment and are hidden in reprs.
- All user-derived HTML is escaped before `unsafe_allow_html` rendering.
- Uploads are size/page limited, filenames are sanitized, and temp dirs are `0700`.
- The deployment agent requires `DEPLOY-MSM` for mutations and never pushes to a remote.

---

## Troubleshooting

| Symptom | Check |
|---|---|
| Assistant answers from rules only | Chat mode/endpoint, `Test chat model` button |
| Tasks list empty | Configure IMAP or use the customer form in Tasks |
| PDF upload fails | Install poppler; check 10 MB / 10 page limits |
| Theme looks flat/glass unexpectedly | Theme picker badge and contrast setting |
| API rejects bearer tokens | `ECOMOPS_SESSION_SECRET` must match between issuer and verifier |
| Docker build fails | Ensure Docker is installed and port 8501 is free |

---

## Contributing

1. Inspect first: `repo_status`, `list_files`, `read_file` (or plain git/grep).
2. Keep changes small; add a test for new behaviour.
3. Run `ruff` and `pytest` before committing.
4. Add or update docs under `docs/` and regenerate PDFs with the build scripts.
5. If architecture changes, update `msm-architecture-graph.json`, regenerate the
   `.drawio` and PNG, and re-export the PDFs.
6. Never commit secrets; `.env` is ignored.

See [`AGENTS.md`](AGENTS.md) for the deployment agent operating rules.

---

## License

MIT. See [LICENSE](LICENSE).

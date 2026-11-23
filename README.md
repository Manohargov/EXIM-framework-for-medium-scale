# ecomops — MSM operations console for an event-driven multi-agent system

Four specialised LLM-driven agents handle **customer requirements**, **stock
clearance**, **transit management** and **returns & refunds**. They are
connected by an event bus and an approval gate rather than sharing one
conversation, so each agent keeps a single operational focus while the system
coordinates sales, inventory and logistics without a human relaying every
message by hand.

```
                 ┌──────────────────────────────────────────────────────────┐
 Customer ──────►│  Customer Requirements Agent                             │
 request         │    capture intent · match catalogue · emit demand        │
                 └───────┬───────────────────────────────┬──────────────────┘
                         │ DemandSignal                  │ return/complaint intent
                         ▼                               ▼
       ┌──────────────────────────────┐   ┌──────────────────────────────────┐
 WMS ─►│  Stock Clearance Agent       │   │  Returns & Refunds Agent         │
       │  discount · reserve          │   │  assess · refund · replacement   │
       └──────────────┬───────────────┘   └──────────────┬───────────────────┘
                      │ StockReserved                    │ RefundIssued /
                      ▼                                  │ ReplacementAuthorised
       ┌──────────────────────────────┐                  │
Carrier►│  Transit Management Agent    │                  │
       │  route · resolve exceptions  │                  │
       └──────────────┬───────────────┘                  │
                      └──────────────┬───────────────────┘
                                     ▼
                    every side-effecting action passes through the
                    ┌────────────────────────────────────────────┐
                    │  Approval Gate  (policy + human-in-loop)   │
                    │  discount ≤ 20% · qty ≤ 10 · value ≤ $200  │
                    │  refunds always need a human               │
                    └────────────────────────────────────────────┘
```

## Quick start

The application lives in the `msm/` folder and ships with a ready-to-run
`.venv` (paths already rewritten for this location). To run the console:

```bash
cd msm
.venv/bin/python -m streamlit run src/ecomops/app.py --server.port 8501
```

The console **menu sits in the top-right**, beside the Theme switcher. Every
workspace is a raised, 3D-styled icon button with a four-hue outline; hover an
icon to see its name. Each role sees only the workspaces it is permitted to
open, and the selected view persists across actions. The sidebar keeps the
assistant, configuration and sign-out; the assistant starts collapsed there.

The **Theme** pill in the top-right corner switches between six palettes. Three
use the frosted-glass material (Obsidian Gold, Midnight Sapphire, Emerald Ledger)
and three use a flat, solid-surface style (Terracotta Atelier, Rose Quartz,
Graphite & Silver). Each palette carries four distinct hues and offers Soft and
Crisp contrast levels. The choice only re-skins the console — posters and
domain output keep their own branded colours.

### Tasks from mail and forms

The **Tasks** tab is the operator dashboard. It shows actionable items extracted
from two sources:

* **IMAP inbox** — configure host, port, user, password/app token, folder and
  message limit under *Configuration → Inbox (IMAP)* in the sidebar, then press
  **Read inbox** in the Tasks tab. TLS is the default; scanning is read-only
  unless *Mark as read* is enabled. Defaults can be supplied with
  `ECOMOPS_MAIL_HOST`, `ECOMOPS_MAIL_PORT`, `ECOMOPS_MAIL_USER`,
  `ECOMOPS_MAIL_PASSWORD`, `ECOMOPS_MAIL_FOLDER`, `ECOMOPS_MAIL_LIMIT`,
  `ECOMOPS_MAIL_SSL` and `ECOMOPS_MAIL_MARK_SEEN`. Credentials never leave the
  browser session and are never written to the event journal.
* **Customer form** — *Create tasks from a customer form* in the Tasks tab feeds
  free text through the same extractor without needing an inbox connection.

Both sources publish `EmailReceived` / `FormReceived` events. The task agent
extracts `TaskExtracted` items with the deterministic offline rule, or with a
configured local model through the existing fallback path. Tasks are scoped to
the signed-in user (`assignee_email`); managers can opt into *All assignees*.
Status changes are append-only `TaskStatusChanged` events, so the bus stays the
single source of truth.

To recreate the environment from scratch instead:

```bash
cd msm
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev,ui,api,chat,docs]"

.venv/bin/ecomops demo --approve yes      # full scenario, auto-approves
.venv/bin/ecomops demo                    # pauses: you decide each approval
.venv/bin/ecomops request "3 mechanical keyboards to Paris ASAP"
.venv/bin/ecomops exceptions              # delivery-exception path
.venv/bin/ecomops stock                   # proactive overstock clearance
.venv/bin/ecomops returns                 # buy, then refund a damaged item

# merge the bundled grocery catalogue (1,481 products) for interface testing
.venv/bin/ecomops catalogue --grocery data/grocery_catalogue.csv --search "peanut butter"
.venv/bin/ecomops request "2 jars of peanut butter to Berlin" \
    --grocery data/grocery_catalogue.csv --approve yes
.venv/bin/ecomops ui --grocery data/grocery_catalogue.csv

.venv/bin/python -m pytest -q             # 137 tests, fully offline (+ UI tests with [ui])
```

Every command accepts `--journal PATH`, which appends each event to a durable
JSONL file. Replay it later, with causal-chain verification:

```bash
.venv/bin/ecomops demo --approve yes --journal events.jsonl
.venv/bin/ecomops replay events.jsonl --verify
.venv/bin/ecomops replay events.jsonl --topic RefundIssued --correlation <id>
```

Add `--mlx` to any command to run a real local model via **mlx-lm** on Apple
Silicon (default `mlx-community/Qwen3-4B-4bit`, cached on this machine). Pick a
bigger model with `--model mlx-community/Qwen3-8B-4bit`, or set
`ECOMOPS_MLX_MODEL`. Install the backend with `uv pip install -e ".[mlx]"`.
If the model is not installed or fails to load, the system falls back to the
deterministic rules and records `llm_backend=fallback:rule-based` on every
event, so you can always see which backend made a decision. Model-size trade-offs
(throughput vs reasoning quality on an M2) are documented in
`../AGENTICAI_PLAN.md` §5.

### MSM console (Streamlit UI)

```bash
uv pip install --python .venv/bin/python -e ".[ui]"
.venv/bin/ecomops ui                      # opens http://localhost:8501
```

The console - branded **MSM** - drives the same `EcomopsSystem` as the CLI: run
the demo scenario, submit a customer request, trigger the stock-clearance pass,
report a carrier exception, simulate a damaged-item return, and grant/deny every
gated action from the Approvals tab. The Overview tab visualises event flow, the
Event trace tab can seed deterministic sample history, and the Inventory tab
shows live prices, stock and reservations. A **Referrals & finance** tab adds
revenue, refunds, markdowns and the referral ledger.

Every session starts at a sign-in gate. Four roles are enforced in the UI, the
API and the assistant:

| Role | Sees | Can do |
|---|---|---|
| **MD** | everything | full access, configuration, all approvals |
| **Supervisor** | operations + event trace | act and approve operations (never finance) |
| **Accountant** | finance only | view finance, approve refunds |
| **Staff** | operations | submit requests, run stock/transit/return actions |

```bash
# demo mode (default): one account per role, password `msm`
.venv/bin/ecomops ui
# md@msm.local · supervisor@msm.local · accountant@msm.local · staff@msm.local

# skip the gate in tests/demos
.venv/bin/ecomops ui --login supervisor      # md | supervisor | accountant | staff
# or: ECOMOPS_AUTH_AUTOLOGIN=supervisor .venv/bin/ecomops ui
```

On this machine the console runs as the user service **`msm-ui`** on port 8501
and restarts automatically:

```bash
systemctl --user status msm-ui      # is it up?
systemctl --user restart msm-ui     # after a code change
journalctl --user -u msm-ui -f      # live log
```

### SSO and authorisation

`ECOMOPS_SSO_MODE` selects how identity reaches MSM:

| Mode | Use it for | What happens |
|---|---|---|
| `dev` (default) | local demo | password form against the four demo accounts; `ECOMOPS_DEMO_PASSWORD` overrides `msm` |
| `oidc` | a real IdP | Streamlit `st.login()` authorization-code flow; configure `[auth]` in `.streamlit/secrets.toml` (Entra ID, Google, Okta, Keycloak…) |
| `proxy` | oauth2-proxy / gatekeeper | trusted `X-Auth-Request-Email` + `X-Auth-Request-Groups` headers, accepted only with a matching `X-Ecomops-Proxy-Secret` |

IdP identities are mapped to roles by e-mail (`ECOMOPS_ROLE_MAP`), then by group
(`ECOMOPS_GROUP_MAP`), then the demo directory, then `ECOMOPS_DEFAULT_ROLE`
(`staff`). Unknown users therefore get the least privilege:

```bash
export ECOMOPS_SSO_MODE=oidc
export ECOMOPS_ROLE_MAP='{"boss@corp.com": "md"}'
export ECOMOPS_GROUP_MAP='{"msm-finance": "accountant", "msm-ops": "supervisor"}'
export ECOMOPS_DEFAULT_ROLE=staff
export ECOMOPS_SESSION_SECRET='a-long-random-string'
```

Permissions are explicit: `view_operations`, `act_operations`,
`approve_operations`, `view_finance`, `approve_finance`, `view_audit`, `manage`.
Money movements (`issue_refund`) need `approve_finance`; everything else needs
`approve_operations`. API sessions are HMAC-signed bearer tokens (8 h TTL).

### Sidebar assistant (LangChain)

The sidebar chat answers stock, approval, shipment, return, audit and finance
questions. If the sidebar is collapsed, the circular **»** button at the top-left
reopens it (the **Assistant** toggle inside the sidebar hides/shows just the chat).
Deterministic intents run first (and enforce the same role matrix);
if `langchain-core` + `langchain-openai` are installed it then calls any
OpenAI-compatible endpoint. The default is a local **Lemonade** server with
**Llama-3.2-1B-Instruct** (GGUF, 0.8 GB — under the 1.5 GB cap):

```bash
# one-time: download + load the small chat model (needs local Lemonade)
lemonade pull Llama-3.2-1B-Instruct-GGUF     # 0.8 GB
lemonade load Llama-3.2-1B-Instruct-GGUF --ctx-size 4096
lemonade status                              # shows the loaded model

uv pip install --python .venv/bin/python -e ".[chat]"
.venv/bin/ecomops ui
```

`ECOMOPS_CHAT_BASE_URL` (default `http://127.0.0.1:13305/api/v1`) and
`ECOMOPS_CHAT_MODEL` (default `Llama-3.2-1B-Instruct-GGUF`) point the assistant
elsewhere. If Lemonade is not running, the model is missing, or
`ECOMOPS_CHAT_DISABLE_LLM=1` is set, answers fall back to the deterministic
summary, so the console never breaks offline.

#### GGUF connector

`src/ecomops/chatmodel.py` is a drop-in connector with two modes, configured
either in *Configuration → Chat model* in the sidebar or with environment vars:

* **Local endpoint** (`ECOMOPS_CHAT_MODE=endpoint`) — connect to any running
  OpenAI-compatible server (`ECOMOPS_CHAT_BASE_URL`, `ECOMOPS_CHAT_MODEL`,
  `ECOMOPS_CHAT_API_KEY`). Leave the model id blank to auto-discover the first
  loaded model. The connector also works with llama.cpp's `llama-server`,
  Ollama and vLLM.
* **GGUF file** (`ECOMOPS_CHAT_MODE=gguf`, `ECOMOPS_CHAT_GGUF=/path/to/chat.gguf`)
  — the connector starts `llama-server` for that file on demand
  (`ECOMOPS_CHAT_PORT` default `38285`, `ECOMOPS_CHAT_CTX` default `8192`,
  `ECOMOPS_CHAT_NGL=-1` uses all GPU layers), and stops the process it started
  on shutdown. Use **Test chat model** in the sidebar to verify it.

When you later add a GGUF, set the mode to *GGUF file*, enter its path and press
**Test chat model** — no code change needed. The assistant falls back to the
deterministic rules until the model answers, and rejects invented numbers so a
small model cannot leak false figures into the console.

### Document intake

Attach a PDF, PNG or JPEG with the composer's attachment control. MSM reads the
document locally, classifies it (purchase order, return/refund slip, stock
sheet, or generic), fills every field it can answer from the catalogue and the
event store, and asks only for the rest:

```bash
# text-layer PDFs need nothing beyond poppler
.venv/bin/ecomops doc extract tests/fixtures/documents/return_slip.pdf
.venv/bin/ecomops doc fill tests/fixtures/documents/return_slip.pdf \
    -a sku=SKU-1001 -a shipment_id= --mode original
```

Required gaps block the fill; optional gaps (such as a shipment id) are
reported but never block. Answers are re-resolved, so a corrected SKU also
pulls its description, price and stock from the catalogue. Output is a PDF in
the original geometry (default) or a clean re-flowed rebuild, always with an
"Answers & sources" annex. Scans and photos need `tesseract-ocr`:

```bash
sudo apt-get install -y tesseract-ocr tesseract-ocr-eng tesseract-ocr-osd
```

`ECOMOPS_OCR_LANGS` (default `eng`) selects the OCR languages. See
`docs/document-intake-plan.md` for the architecture and known limits.

### HTTP API

```bash
uv pip install --python .venv/bin/python -e ".[api]"
.venv/bin/ecomops serve --port 8000      # OpenAPI docs at /docs
.venv/bin/ecomops serve --no-auth        # local demo only: no bearer tokens
```

The API is a third adapter over the same `EcomopsSystem` (the CLI and UI are the
other two). It exposes the entry points and read models, so an existing web form,
WMS or carrier webhook can write into the system without shelling out to Python.
Authentication is on by default: log in once, then send the bearer token.

```bash
TOKEN=$(curl -s localhost:8000/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"md@msm.local","password":"msm"}' | python -c 'import json,sys;print(json.load(sys.stdin)["token"])')
curl -s localhost:8000/auth/me -H "authorization: Bearer $TOKEN"
```

| Method | Path | Permission | Purpose |
|---|---|---|---|
| `POST` | `/auth/login` | — | demo/SSO-offload login → bearer token |
| `GET` | `/auth/me` | any | current user + permissions |
| `POST` | `/requests` | act_operations | `{customer_id, text, channel}` → run the pipeline |
| `POST` | `/stock-report` | act_operations | proactive clearance (`min_age_days`, `min_stock`, `max_items`) |
| `POST` | `/exceptions` | act_operations | carrier webhook (`shipment_id`, `exception`, `detail`) |
| `POST` | `/approvals/{proposal_id}` | approve_operations / approve_finance | `{granted, decided_by, comment}` |
| `GET` | `/approvals` | approval role | queue filtered to what your role may decide |
| `GET` | `/events` | view_audit | audit trail (`topic`, `correlation_id`, `limit`) |
| `GET` | `/inventory` | view_operations | products (+ ratings/descriptions), counts by source/category, reservations |
| `GET` | `/shipments` | view_operations | routed shipments |
| `GET` | `/catalogue/match` | view_operations | rank products for `query` (same matcher the agent uses) |
| `POST` | `/documents` | any signed-in | base64 PDF/PNG/JPEG → resolved document job |
| `GET` | `/documents` | any signed-in | open document jobs |
| `GET` | `/documents/{id}` | any signed-in | one job with rows, sources and the review message |
| `POST` | `/documents/{id}/answers` | any signed-in | `{answers: {sku: …}}` or `{text: "sku=…"}` |
| `POST` | `/documents/{id}/fill` | any signed-in | `{mode: "original"\|"reflow"}` → PDF download |
| `DELETE` | `/documents/{id}` | any signed-in | discard the temp job |
| `GET` | `/finance` | view_finance | revenue, refunds, markdowns, referral ledger |
| `GET` | `/metrics` | view_operations | Prometheus text (`ecomops_*` counters/gauges) |
| `GET` | `/healthz` | — | liveness + backend + queue depth |

```bash
curl -s localhost:8000/requests \
  -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"customer_id":"CUST-1","text":"2 wireless bluetooth earbuds to Berlin ASAP"}'
curl -s localhost:8000/approvals -H "authorization: Bearer $TOKEN"
curl -s 'localhost:8000/catalogue/match?query=paper%20towels' -H "authorization: Bearer $TOKEN"
curl -s localhost:8000/finance -H "authorization: Bearer $TOKEN"
curl -s localhost:8000/metrics -H "authorization: Bearer $TOKEN" | grep ecomops_refunds
```

In `proxy` mode the reverse proxy supplies the identity instead of a bearer token.
`create_app(require_auth=False)` disables the gate for scripts and tests.

A single in-process `asyncio.Lock` serialises bus drains. For horizontal
scaling, replace the bus (Redis Streams/NATS) — the API and agents do not change.

## Grocery catalogue (interface testing)

`data/grocery_catalogue.csv` is the public *Grocery Dataset* export: 1,757 rows
across 19 sub-categories with prices, discounts, ratings, features and marketing
copy (source: Kaggle, kept locally for demo/testing). It is used as **realistic
demo data for the UI/API**, not for model training — there is no classifier,
embedding index or fine-tune here.

```bash
.venv/bin/ecomops catalogue --grocery data/grocery_catalogue.csv   # browse
.venv/bin/ecomops catalogue --grocery data/grocery_catalogue.csv --match "olive oil"
.venv/bin/ecomops ui --grocery data/grocery_catalogue.csv          # merged UI
.venv/bin/ecomops serve --grocery data/grocery_catalogue.csv       # merged API
```

`load_grocery_catalogue` cleans the CSV deterministically, so the same file always
produces the same SKUs:

* prices are parsed (`$1,999.99 ` → `1999.99`), commas included;
* `After $5 OFF` / `After $5 - $8 OFF` become a numeric discount;
* `Rated 4.3 out of 5 stars based on 265 reviews.` becomes rating + review count;
* duplicate titles (250 in the file) collapse to one product, junk rows
  (`Page Not Found!`, no usable price) are skipped — 1,481 products load;
* stock and age come from a stable hash of the title, so the catalogue mixes
  healthy, ageing and out-of-stock lines (~8%) without a warehouse feed;
* products keep the original sub-category as their category, and get
  `GRO-####` SKUs after sorting, so a `--grocery-limit` cut never renumbers the
  rows it keeps.

The ten built-in demo SKUs stay in front, so the scenario texts, the event
traces and the existing tests keep their meaning. In the UI, the sidebar has a
**Merge grocery dataset** toggle (on by default when the file is present) plus
search/category filters in the Inventory tab.

Matching a short query against a long marketplace title needed a small lexical
upgrade: the fallback embedder treats *full token coverage* (every token of the
shorter text appears in the longer one) as a strong match, scaled by the extra
text. `"paper towels"` now finds
`"Bounty Advanced Paper Towels, 2-Ply, 101 Sheets, 12-count"` (0.68) while
`"quantum flux capacitor"` still fails (0.10). Because a merged catalogue can
have hundreds of ageing lines, the stock report is capped to the costliest 25 by
default (`--grocery` does not change that; `/stock-report` takes `max_items`).

## What each agent owns

| Agent | Consumes | Produces | Side effects (all gated) |
|---|---|---|---|
| **Customer Requirements** | `CustomerRequestReceived` | `CustomerIntentCaptured`, `DemandSignal`, `IntentNeedsClarification` | none — it never acts, only structures |
| **Stock Clearance** | `DemandSignal`, `StockLevelReport` | `ClearanceStrategyProposed`, `StockReserved`, `DiscountApplied` | reserve stock, apply markdown |
| **Transit Management** | `StockReserved`, `DeliveryExceptionReported` | `ShipmentRouted`, `ExceptionResolutionProposed`, `DeliveryExceptionResolved` | dispatch shipment, resolve exception |
| **Returns & Refunds** | `CustomerIntentCaptured` (return/complaint), `ShipmentRouted` | `ReturnRequested`, `ReturnAssessed`, `ReturnNeedsOrder`, `RefundIssued`, `ReplacementAuthorised`, `ReturnRejected` | issue refund, authorise replacement |

The **approval gate** sits between every proposal and its execution. An agent
publishes `ActionProposed`; the gate assesses it against `PolicyConfig` and
either approves autonomously, raises `ApprovalRequested` for a human, or
rejects and forces an escalation. Nothing that moves money beyond a small
discount, or that ships/destroys stock, runs without a recorded decision.

## Design decisions

* **Deterministic in-process bus.** `publish` enqueues, `drain` processes FIFO
  and awaits each handler. The same inputs always yield the same event
  sequence — which is what makes the tests and the demo reproducible with no
  broker. The `subscribe/publish/drain/history` contract is small enough to swap
  for Redis Streams or NATS without touching agent code.
* **Correlation + causation for free.** An event published while handling
  another inherits its `correlation_id` and records its own `causation_id`, so a
  whole business conversation can be replayed from `bus.conversation(id)`.
* **Ambiguity escalates, it never guesses.** A product the customer names that
  cannot be matched to the catalogue emits `IntentNeedsClarification` instead of
  reserving the wrong thing.
* **LLM with a deterministic fallback.** Agents call `llm.complete(task=...)`;
  the local MLX backend and the rule-based backend return the same shapes and are
  validated by the same pydantic schemas, so a fallback run exercises identical
  downstream logic. The model does the interpretive step (choosing a route, a
  markdown, a resolution); the rules keep the system honest and offline-runnable.
* **Semantic matching.** Catalogue matching and substitution use
  `all-MiniLM-L6-v2` via sentence-transformers when installed, degrading to a
  pure-Python token-overlap embedder otherwise. The fallback also scores full
  token coverage so short queries match long marketplace titles (see the grocery
  catalogue section).
* **Returns close the loop.** The customer agent already captured return and
  complaint intent; the Returns agent turns that into an assessed decision.
  It matches against `ShipmentRouted` events rather than reading the transit
  agent's table, and a delivered item can only be claimed once.
* **The audit trail outlives the process.** `--journal` appends every event to
  JSONL; `replay --verify` rebuilds typed events and checks that every
  causation id points at an earlier record. There is no database to operate.
* **Operations are observable.** `/metrics` derives counters from the event
  history rather than a parallel metrics store — if it happened, it is counted.

## Business value

Reduced manual coordination across four workflows: an agent's output becomes
another's input through the bus, so no person has to relay stock, shipment or
return state between teams. The project shows Agentic AI organised around
operational responsibilities rather than one general-purpose chatbot.

## What matters when extending this

A client rollout should define **agent permissions**, **escalation paths** and
**which actions require human approval** — all three live in `PolicyConfig` and
the `EscalationRaised` flow, and are the first things to configure. Evaluation
should cover **intent capture**, **coordination accuracy** and **exception
resolution**; the test suite is a starting harness for all three. Inventory and
shipment changes should be assessed against existing business controls before
autonomous execution is widened. Returns add `ReturnPolicy` (window, restocking
fee, damaged-goods handling) as a second policy surface; the journal and
`/metrics` endpoint are the audit and observability seams to connect to existing
controls. No quantified throughput, revenue or deployment scale is claimed here.

## Deployment Deep Agent + local repo

`deploy_agent/` is a LangChain **Deep Agents** automation agent backed by a local
OpenAI-compatible GGUF runtime (llama-server). It inspects the repo, runs tests,
checks health, and can generate deployment files, commit locally, and manage the
local Docker lifecycle — mutating tools require the phrase `DEPLOY-MSM` in the
request, and no tool pushes to a remote.

```bash
export ECOMOPS_DEEPAGENTS_BASE_URL=http://127.0.0.1:38285/v1
export ECOMOPS_DEEPAGENTS_MODEL=MiMo-V2.6-Distill-Qwen-9B-Q8_0
export ECOMOPS_DEEPAGENTS_API_KEY=<local-llama-server-key>
.venv/bin/python -m deploy_agent.cli "check repo status and run the tests"
```

The repo was initialised locally with one commit (`git log --oneline`), and
`Dockerfile`, `docker-compose.yml` and `.dockerignore` provide the local Docker
deployment target. See `deploy_agent/README.md` for the full tool/safety table.

## User guide

A detailed 21-page PDF guide with screenshots of every workspace, workflow
instructions, roles, themes, assistant/document intake, chat connector,
deployment agent and architecture diagrams is at:

`docs/user-guide/MSM_User_Guide.pdf` (rebuild with `docs/user-guide/build_guide.py`).

## Developer document

A source-grounded 16-page developer document with architecture diagrams, Pydantic
data schemas, storage model, UI/UX design tokens, AI/ML integration, runtime flows,
API/CLI, deployment and security/operations is at:

`docs/developer-doc/MSM_Developer_Document.pdf` (rebuild with
`docs/developer-doc/build_developer_doc.py`).

## Architecture diagrams

Editable draw.io sources and rendered PNGs live in `docs/architecture/`:

- `msm-architecture.drawio` / `.png` — the whole console: entry points, auth,
  UI shell, composition root, event bus, five agents, data/renderers, storage
  and the chat connector.
- `msm-chat-connector.drawio` / `.png` — a focused view of the chat connector:
  config → modes (off / endpoint / GGUF) → LangChain → local model → grounding
  guard → answer or rule fallback.

Both are editable in draw.io; the `.json` files beside them are the graph
sources used for deterministic auto-layout.

## Layout

```
msm/
  src/ecomops/
    events.py              typed domain events (the message catalogue)
    bus.py                 in-process event bus, correlation/causation, replay
    journal.py             durable JSONL event log + causal verification
    llm.py                 MLX (mlx-lm) + rule-based fallback, embedders
    rules.py               deterministic handlers for each LLM task
    catalogue.py           GroceryDataset.csv -> cleaned demo catalogue (no training)
    inventory.py           catalogue, stock, ageing, reservations, restock
    policy.py              approval gate: policy engine + human decisions
    auth.py                roles, permissions, signed sessions, SSO mapping
    finance.py             finance + referral read models
    chat.py                sidebar assistant (rules + LangChain)
    metrics.py             event-derived counters + Prometheus text
    system.py              composition root (bus + gate + 4 agents)
    api.py                 FastAPI control plane (optional [api] extra)
    cli.py                 demo / request / exceptions / stock / returns /
                           catalogue / replay / serve / ui
    app.py                 MSM console, Streamlit (optional [ui] extra)
    agents/
      base.py              propose → approve → act handshake
      customer.py          Customer Requirements Agent
      clearance.py         Stock Clearance Agent
      transit.py           Transit Management Agent
      returns.py           Returns & Refunds Agent
  data/grocery_catalogue.csv  GroceryDataset.csv export (interface-test data)
  data/image_cache/         cached web images for poster generation (regenerable)
  docs/                     document-intake plan + architecture diagrams
  src/ecomops/mail.py       stdlib IMAP reader (injectable client, offline tests)
  src/ecomops/tasks.py      task dashboard read model + status transitions
  src/ecomops/agents/tasks.py  email/form -> TaskExtracted agent
  tests/                   258 tests, offline, deterministic (+ UI smoke tests)
  .streamlit/config.toml    console theme and browser settings
  .venv/                    bundled environment (relocated; paths already fixed)
  .github/workflows/ci.yml ruff + import check + pytest + demo
```

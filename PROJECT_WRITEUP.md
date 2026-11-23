# MSM Operations Console — Project Write-up

## 1. Executive summary

**MSM** is a medium-scale, event-driven operations framework for e-commerce. It unifies
customer requirements, stock clearance, transit management, returns/refunds, finance
read models, task extraction and a role-based web console into one deterministic,
offline-first system.

The project was built to demonstrate how a real operations team can coordinate several
specialised agents without a database or message broker: a typed in-process event bus
acts as the single source of truth, an approval gate protects every side effect, and
deterministic rules guarantee that the system always works — even when no AI model is
available.

The deliverable includes:

- a Streamlit console with ten permission-driven workspaces,
- a CLI and an optional FastAPI control plane,
- five domain agents plus a task-extraction agent,
- IMAP-based task extraction and a customer form pipeline,
- document intake with PDF/image extraction and filled output,
- poster and notice generation,
- six four-hue UI themes (three glass, three flat),
- a local chat-model connector with GGUF support,
- a LangChain Deep Agents deployment agent,
- Docker assets, 293 tests, and full user/developer documentation.

Repository: <https://github.com/Manohargov/EXIM-framework-for-medium-scale>

---

## 2. Problem and objectives

### Problem

Medium-scale commerce operations typically span disconnected tools: email inboxes,
spreadsheets, warehouse exports, carrier portals and finance reports. Important work is
lost between systems, approvals are informal, and there is no shared audit trail.
Adding an LLM to such a system often makes it less reliable, not more.

### Objectives

1. **One coordination model** — every business fact becomes a typed event with
   correlation and causation metadata.
2. **Deterministic by default** — rule-based handlers cover every LLM task; AI is
   optional and must degrade safely.
3. **Human control** — risky or high-value actions require approval before execution.
4. **Role-aware access** — MD, Supervisor, Staff and Accountant see only what they are
   entitled to see.
5. **Operational visibility** — dashboards, KPIs, event trace and metrics are derived
   from the event history, not a separate reporting store.
6. **Practical extensibility** — new events, rules, agents, channels and themes can be
   added without rewriting the core.

---

## 3. Solution overview

```text
UI (Streamlit) ─┐
CLI ────────────┼──> EcomopsSystem ──> EventBus ──> 5 domain agents
API (FastAPI) ──┘        │                │             │
                         ├─ ApprovalGate   ├─ Journal    ├─ Inventory
                         ├─ LLM + Rules    ├─ Metrics    ├─ Finance
                         └─ Embedder       └─ Tasks      └─ Documents/Renderers
```

The console, CLI and API all share the same `EcomopsSystem` composition root. It wires
the event bus, approval gate, LLM interface, embedder, inventory and agents. Agents react
to subscribed events, ask the LLM interface for structured JSON, validate it with
Pydantic, and publish new events. They never call each other directly.

Side effects follow a strict handshake:

```text
ActionProposed -> ApprovalRequested -> ActionApproved / ActionRejected -> act()
```

---

## 4. Key capabilities

### 4.1 Operations workflows

| Flow | Outcome |
|---|---|
| Customer request | Intent capture, SKU matching, demand signals, clarification when ambiguous |
| Stock clearance | Overstock detection, discount strategy proposals, approval-gated discounts |
| Transit | Route planning, shipment routing, carrier exceptions and resolution |
| Returns | Damaged-item assessment, refund or replacement proposals |
| Finance | Revenue, refunds, markdown value, referral ledger from recorded events |
| Tasks | Email/form extraction into a per-user task board with status transitions |

### 4.2 Console UX

- Top-right **3D icon navigation** with hover tooltips; active view persists across actions.
- Compact **KPI rail** showing label and number only.
- **Six themes** with four distinct hues each: three frosted-glass and three flat.
- Sidebar assistant, configuration and sign-out; role-based workspaces.
- All user-derived HTML is escaped before injection.

### 4.3 Intelligence

Eight deterministic rule handlers cover every LLM task: intent capture, task extraction,
clearance strategy, route planning, exception resolution, return assessment and two copy
writers. An optional local MLX model (`mlx-community/Qwen3-4B-4bit`) can replace rules
through a fallback wrapper. Embeddings use `all-MiniLM-L6-v2` when available, otherwise
token overlap.

The sidebar assistant adds a separate chat connector supporting **off**, **endpoint**
(any OpenAI-compatible server) and **gguf** (auto-started llama-server) modes, with a
number-grounding guard that rejects invented figures.

### 4.4 Documents and creatives

Uploaded PDFs and images are extracted, classified and resolved against catalogue and
bus data. Missing required fields are requested; the result is a filled downloadable PDF.
Clearance posters and transit notices are rendered with Pillow, with web image search
and a disk cache, and published through channel adapters.

---

## 5. Technology and architecture decisions

| Decision | Rationale |
|---|---|
| Event bus instead of broker/database | Fully offline, deterministic, easy to test and replay |
| Pydantic v2 models | Validation, schema export to LLM prompts, strict event envelopes |
| Rule-first LLM interface | No model dependency; predictable demos; safe fallback |
| Approval gate | Auditability and human control over money/operations side effects |
| CSS-variable themes | Skin changes without widget rebuilds or domain changes |
| Streamlit single page with custom shell | Fast delivery of a multi-workspace console |
| FastAPI as optional plane | Separate automation/integration surface |
| LangChain Deep Agents | Production-grade planning/tool harness for deployment automation |

---

## 6. Data and storage

There is no relational database. State lives in:

- **EventBus** — in-memory typed event history with correlation/causation.
- **EventJournal** — optional append-only JSONL audit log.
- **Streamlit session state** — user, active view, theme, chat history, document and
  connection settings.
- **Inventory** — in-memory catalogue from built-in SKUs plus an optional 1,481-row CSV.
- **Task state** — a pure fold of `TaskExtracted` and `TaskStatusChanged` events.
- **Files** — image cache, document workdirs (`0700`), generated posters/notices.

---

## 7. Project structure

```text
msm/
├── src/ecomops/        application package
├── deploy_agent/       Deep Agents deployment automation
├── data/               catalogue and image cache
├── docs/               architecture diagrams, user guide, developer document
├── tests/              293 offline tests
├── Dockerfile
├── docker-compose.yml
├── README.md
└── PROJECT_WRITEUP.md  this document
```

---

## 8. Delivery and verification

- `ruff check src tests deploy_agent` — clean
- `pytest -q` — **293 passed**
- Streamlit health endpoint — `ok`
- Browser smoke tests — all workspaces, themes, task creation and approvals verified
- Browser device login used for GitHub; no token stored in source
- Code pushed to `Manohargov/EXIM-framework-for-medium-scale`, branch `master`

---

## 9. Security posture

- Demo mode with password `msm` is for local use only; real deployments should use
  OIDC/proxy mode and TLS.
- `ECOMOPS_SESSION_SECRET` must be set before exposing the FastAPI service.
- Secrets are held in session state or environment and hidden from reprs and events.
- Uploads are size/page limited, filenames sanitized, subprocess calls use argument lists.
- The deployment agent requires `DEPLOY-MSM` for mutating tools and never pushes.

---

## 10. Future enhancements

1. Add the CI workflow with a token that has the `workflow` scope.
2. Add persistent storage/backlog for events to support long-running deployments.
3. Add SSO/OIDC configuration example and reverse-proxy template.
4. Expand the local-model connector with tool-calling evaluation for the assistant.
5. Add RBAC-aware API ownership checks for document endpoints.
6. Add rate limiting and request-size limits to the HTTP API.
7. Publish a packaged Docker image and a one-command installer.

---

## 11. Conclusion

MSM demonstrates that a multi-agent operations system can be reliable, explainable and
useful without depending on a cloud LLM or external infrastructure. The event bus
provides a complete audit trail, the rule layer keeps the system functional offline,
the approval gate keeps humans in control, and the console turns recorded events into
clear dashboards and actionable tasks. The optional model paths and deployment agent
show how AI can be added where it helps without making correctness dependent on it.

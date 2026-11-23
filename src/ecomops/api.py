"""HTTP control plane for ecomops (optional ``[api]`` extra).

The CLI and the Streamlit UI are two adapters over the same ``EcomopsSystem``;
this is a third. It exposes the system's existing entry points and read models,
not a second business logic:

    POST /requests                 <- customer web form / chat
    POST /stock-report             <- warehouse WMS
    POST /exceptions               <- carrier webhook
    POST /approvals/{proposal_id}  <- operator approval portal
    GET  /events                   <- audit trail with correlation ids
    GET  /approvals                <- human-in-the-loop queue
    GET  /inventory, /shipments    <- read models
    GET  /catalogue/match?query=…  <- rank the catalogue (same matcher as the agent)
    POST /documents                <- base64 PDF/image -> resolved job
    POST /documents/{id}/answers   <- operator corrections
    POST /documents/{id}/fill      <- filled PDF download
    DELETE /documents/{id}         <- discard the temp job
    GET  /metrics                  <- Prometheus text
    GET  /healthz

A single asyncio lock serialises bus drains: the in-process bus is deterministic
FIFO, so concurrent HTTP writes must not interleave. For horizontal scaling the
bus is the seam to replace (Redis Streams/NATS), not this module.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .auth import (
    FINANCE_ACTIONS,
    AuthError,
    Permission,
    Role,
    SessionCodec,
    User,
    dev_login,
    proxy_secret_ok,
    sso_mode,
    user_from_proxy_headers,
)
from .events import ExceptionType
from .finance import finance_snapshot
from .metrics import prometheus
from .system import EcomopsSystem, build_system


class LoginIn(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=1)


class RequestIn(BaseModel):
    customer_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    channel: str = "api"


class StockReportIn(BaseModel):
    min_age_days: int = 90
    min_stock: int = 100
    max_items: int = Field(25, ge=0)  # clearance pass handles the costliest N lines


class ExceptionIn(BaseModel):
    shipment_id: str = Field(min_length=1)
    exception: ExceptionType
    detail: str = ""


class DecisionIn(BaseModel):
    granted: bool
    decided_by: str = "operator-api"
    comment: str = ""


class DocumentIn(BaseModel):
    """A document upload without multipart: base64 keeps the API dependency-free."""

    filename: str = Field(min_length=1, max_length=160)
    content_base64: str = Field(min_length=1)


class DocumentAnswersIn(BaseModel):
    answers: dict[str, str] = Field(default_factory=dict)
    text: str = ""


class DocumentFillIn(BaseModel):
    mode: str = "original"  # original | reflow


@asynccontextmanager
async def _lifespan(app: FastAPI):
    app.state.lock = asyncio.Lock()
    app.state.documents = {}
    yield
    # Uploads live in temp directories; never leave them behind on shutdown.
    sessions = list(getattr(app.state, "documents", {}).values())
    if sessions:
        from .documents import discard

        for session in sessions:
            discard(session)


def _lock(app: FastAPI) -> asyncio.Lock:
    lock = getattr(app.state, "lock", None)
    if lock is None:
        lock = asyncio.Lock()
        app.state.lock = lock
    return lock


def _status(system: EcomopsSystem) -> dict[str, Any]:
    return {
        "event_count": len(system.bus.history),
        "pending_approvals": [
            {
                "proposal_id": e.proposal_id,
                "action": e.action,
                "reason": e.reason,
                "risk": e.risk.value,
            }
            for e in system.pending_approvals
        ],
    }


def _approval(event) -> dict[str, Any]:
    return {
        "proposal_id": event.proposal_id,
        "action": event.action,
        "parameters": event.parameters,
        "reason": event.reason,
        "risk": event.risk.value,
    }


def _product(product) -> dict[str, Any]:
    return {        "sku": product.sku,
        "name": product.name,
        "category": product.category,
        "unit_price": product.unit_price,
        "stock": product.stock,
        "age_days": product.age_days,
        "inventory_value": round(product.inventory_value, 2),
        "rating": product.rating,
        "reviews": product.reviews,
        "discount": product.discount,
        "source": product.source,
        "description": (
            product.description[:280] + "…"
            if len(product.description) > 280
            else product.description
        ),
    }


def _document_summary(session) -> dict[str, Any]:
    job = session.job
    return {
        "id": job.id,
        "filename": job.filename,
        "doc_type": job.doc_type.value,
        "pages": job.pages,
        "status": job.status,
        "missing": [f.key for f in job.missing],
        "blocking": [f.key for f in job.blocking],
    }


def _document_payload(session) -> dict[str, Any]:
    from .documents import message, rows

    return {
        **_document_summary(session),
        "sha256": session.job.sha256,
        "message": message(session),
        "rows": rows(session.job),
    }


_SYSTEM_USER = User(
    email="system@msm.local", name="System operator", role=Role.MD, provider="system"
)


def _permission_list(user: User) -> list[str]:
    return sorted(permission.value for permission in Permission if user.can(permission))


def create_app(
    system: EcomopsSystem | None = None,
    *,
    use_mlx: bool = False,
    model: str | None = None,
    journal_path: str | None = None,
    grocery: str | None = None,
    grocery_limit: int | None = None,
    require_auth: bool = True,
    session_codec: SessionCodec | None = None,
) -> FastAPI:
    """Build the FastAPI app around one in-process system.

    ``require_auth=False`` is for local scripts and tests only. With auth on,
    every endpoint except ``/healthz`` and ``/auth/login`` needs a bearer token
    (dev login or a reverse-proxy identity) and the role must hold the required
    permission.
    """
    system = system or build_system(
        use_mlx=use_mlx,
        model=model,
        journal_path=journal_path,
        grocery=grocery,
        grocery_limit=grocery_limit,
    )
    codec = session_codec or SessionCodec.from_environment()
    app = FastAPI(
        title="MSM · ecomops",
        version="0.4.0",
        description="Event-driven e-commerce operations: agents, approvals, RBAC, audit trail.",
        lifespan=_lifespan,
    )
    app.state.system = system
    app.state.codec = codec
    app.state.require_auth = require_auth

    # ------------------------------------------------------------ auth gate

    def current_user(request: Request) -> User:
        if not require_auth:
            return _SYSTEM_USER
        headers = dict(request.headers)
        if sso_mode() == "proxy" and proxy_secret_ok(headers):
            try:
                return user_from_proxy_headers(headers)
            except AuthError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            try:
                return codec.verify(header[7:].strip())
            except AuthError as exc:
                raise HTTPException(
                    status_code=401,
                    detail=str(exc),
                    headers={"WWW-Authenticate": "Bearer"},
                ) from exc
        raise HTTPException(
            status_code=401,
            detail="authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    def guard(*permissions: Permission):
        def dependency(user: User = Depends(current_user)) -> User:
            if permissions and not user.can_any(*permissions):
                wanted = ", ".join(p.value for p in permissions)
                raise HTTPException(
                    status_code=403,
                    detail=f"{user.label} lacks permission: {wanted}",
                )
            return user

        return dependency

    # ----------------------------------------------------------------- auth

    @app.post("/auth/login")
    async def login(body: LoginIn) -> dict[str, Any]:
        if sso_mode() != "dev":
            raise HTTPException(
                status_code=403,
                detail="password login is disabled; authenticate through the SSO provider",
            )
        try:
            user = dev_login(body.email, body.password)
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return {
            "token": codec.issue(user),
            "user": user.as_dict(),
            "permissions": _permission_list(user),
        }

    @app.get("/auth/me")
    async def me(user: User = Depends(current_user)) -> dict[str, Any]:
        return {"user": user.as_dict(), "permissions": _permission_list(user)}

    # ------------------------------------------------------------- write side

    @app.post("/requests")
    async def create_request(
        body: RequestIn, user: User = Depends(guard(Permission.ACT_OPERATIONS))
    ) -> dict[str, Any]:
        async with _lock(app):
            await system.receive_customer_request(
                body.customer_id, body.text, channel=body.channel
            )
        return _status(system)

    @app.post("/stock-report")
    async def stock_report(
        body: StockReportIn, user: User = Depends(guard(Permission.ACT_OPERATIONS))
    ) -> dict[str, Any]:
        async with _lock(app):
            await system.report_stock_levels(
                min_age_days=body.min_age_days,
                min_stock=body.min_stock,
                max_items=body.max_items,
            )
        return _status(system)

    @app.post("/exceptions")
    async def report_exception(
        body: ExceptionIn, user: User = Depends(guard(Permission.ACT_OPERATIONS))
    ) -> dict[str, Any]:
        async with _lock(app):
            await system.report_delivery_exception(
                body.shipment_id, body.exception.value, body.detail
            )
        return _status(system)

    @app.post("/approvals/{proposal_id}")
    async def decide(
        proposal_id: str, body: DecisionIn, user: User = Depends(current_user)
    ) -> dict[str, Any]:
        pending = {e.proposal_id: e for e in system.pending_approvals}
        event = pending.get(proposal_id)
        if event is None:
            raise HTTPException(status_code=404, detail=f"no pending proposal {proposal_id!r}")
        # Money movements need a finance approver; everything else needs an
        # operations approver. The MD holds both.
        required = (
            Permission.APPROVE_FINANCE
            if event.action in FINANCE_ACTIONS
            else Permission.APPROVE_OPERATIONS
        )
        if not user.can(required):
            raise HTTPException(
                status_code=403,
                detail=f"{user.label} cannot approve {event.action!r} (needs {required.value})",
            )
        async with _lock(app):
            await system.decide(
                proposal_id,
                granted=body.granted,
                decided_by=body.decided_by or user.email,
                comment=body.comment,
            )
        return _status(system)

    # -------------------------------------------------------------- read side

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "backend": getattr(system.llm, "backend_name", "rule-based"),
            "events": len(system.bus.history),
            "pending_approvals": len(system.pending_approvals),
        }

    @app.get("/metrics", response_class=PlainTextResponse)
    async def metrics(user: User = Depends(guard(Permission.VIEW_OPERATIONS))) -> str:
        return prometheus(system.metrics())

    @app.get("/finance")
    async def finance(user: User = Depends(guard(Permission.VIEW_FINANCE))) -> dict[str, Any]:
        return finance_snapshot(system)

    @app.get("/events")
    async def events(
        topic: list[str] | None = Query(None),
        correlation_id: str | None = Query(None),
        limit: int = Query(100, ge=1, le=5000),
        user: User = Depends(guard(Permission.VIEW_AUDIT)),
    ) -> dict[str, Any]:
        wanted = set(topic or [])
        selected = [
            r for r in system.bus.history
            if (not wanted or r.topic in wanted)
            and (not correlation_id or (r.event.correlation_id or "").startswith(correlation_id))
        ]
        return {"count": len(selected), "events": [r.as_dict() for r in selected[-limit:]]}

    @app.get("/approvals")
    async def approvals(user: User = Depends(current_user)) -> dict[str, Any]:
        if not user.can_any(Permission.APPROVE_OPERATIONS, Permission.APPROVE_FINANCE):
            raise HTTPException(status_code=403, detail="your role cannot view approvals")
        pending = [
            _approval(e)
            for e in system.pending_approvals
            if user.can(
                Permission.APPROVE_FINANCE
                if e.action in FINANCE_ACTIONS
                else Permission.APPROVE_OPERATIONS
            )
        ]
        return {"count": len(pending), "approvals": pending}

    @app.get("/inventory")
    async def inventory(
        user: User = Depends(guard(Permission.VIEW_OPERATIONS)),
    ) -> dict[str, Any]:
        products = [_product(p) for p in system.inventory.products.values()]
        by_source: dict[str, int] = {}
        by_category: dict[str, int] = {}
        for product in products:
            by_source[product["source"]] = by_source.get(product["source"], 0) + 1
            by_category[product["category"]] = by_category.get(product["category"], 0) + 1
        reservations = [
            {
                "reservation_id": r.reservation_id,
                "sku": r.sku,
                "quantity": r.quantity,
                "customer_id": r.customer_id,
                "order_id": r.order_id,
            }
            for r in system.inventory.reservations.values()
        ]
        report = system.grocery_report
        return {
            "count": len(products),
            "by_source": by_source,
            "by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
            "grocery": None if report is None else {
                "file": report.path.name,
                "rows": report.rows,
                "loaded": report.loaded,
                "duplicates": report.duplicates,
                "skipped": report.skipped,
            },
            "products": products,
            "reservations": reservations,
        }

    @app.get("/catalogue/match")
    async def catalogue_match(
        query: str = Query(..., min_length=1, description="Free-text product query"),
        threshold: float = Query(0.25, ge=0.0, le=1.0),
        limit: int = Query(5, ge=1, le=50),
        user: User = Depends(guard(Permission.VIEW_OPERATIONS)),
    ) -> dict[str, Any]:
        """Rank the catalogue for a query — the same matcher the customer agent uses."""
        ranked = system.inventory.rank(query)[:limit]
        best, best_score = ranked[0] if ranked else (None, 0.0)
        return {
            "query": query,
            "threshold": threshold,
            "matched": best is not None and best_score >= threshold,
            "best": None if best is None else {**_product(best), "score": round(best_score, 4)},
            "candidates": [
                {"sku": p.sku, "name": p.name, "score": round(score, 4)}
                for p, score in ranked
            ],
        }

    @app.get("/shipments")
    async def shipments(
        user: User = Depends(guard(Permission.VIEW_OPERATIONS)),
    ) -> dict[str, Any]:
        return {"shipments": system.shipments()}

    # -------------------------------------------------------------- documents

    def document_session(job_id: str):
        session = app.state.documents.get(job_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"no document job {job_id!r}")
        return session

    @app.post("/documents", status_code=201)
    async def upload_document(
        body: DocumentIn, user: User = Depends(current_user)
    ) -> dict[str, Any]:
        """Read a base64 PDF/PNG/JPEG, resolve its fields, and keep the job."""
        from .documents import (
            DocumentError,
            ExtractionError,
            OcrUnavailable,
            discard,
            make_workdir,
            open_document,
        )

        try:
            data = base64.b64decode(body.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(
                status_code=400, detail="content_base64 is not valid base64"
            ) from exc
        if not data:
            raise HTTPException(status_code=400, detail="the document is empty")

        name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(body.filename).name).strip("._")
        folder = make_workdir()
        path = folder / (name or "document")[:80]
        path.write_bytes(data)
        try:
            session = open_document(
                system, user, path, workdir=folder, filename=body.filename
            )
        except (DocumentError, ExtractionError, OcrUnavailable) as exc:
            shutil.rmtree(folder, ignore_errors=True)
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        previous = app.state.documents.pop(session.job.id, None)
        if previous is not None:
            discard(previous)
        app.state.documents[session.job.id] = session
        return _document_payload(session)

    @app.get("/documents")
    async def list_documents(user: User = Depends(current_user)) -> dict[str, Any]:
        return {
            "documents": [
                _document_summary(session)
                for session in app.state.documents.values()
            ]
        }

    @app.get("/documents/{job_id}")
    async def get_document(
        job_id: str, user: User = Depends(current_user)
    ) -> dict[str, Any]:
        return _document_payload(document_session(job_id))

    @app.post("/documents/{job_id}/answers")
    async def answer_document(
        job_id: str, body: DocumentAnswersIn, user: User = Depends(current_user)
    ) -> dict[str, Any]:
        from .documents import answer

        session = document_session(job_id)
        text = body.text or "\n".join(f"{key}={value}" for key, value in body.answers.items())
        updated = answer(system, user, session, text)
        return {**_document_payload(session), "updated": updated}

    @app.post("/documents/{job_id}/fill")
    async def fill_document(
        job_id: str, body: DocumentFillIn, user: User = Depends(current_user)
    ):
        from .documents import finish

        if body.mode not in {"original", "reflow"}:
            raise HTTPException(
                status_code=400, detail="mode must be 'original' or 'reflow'"
            )
        session = document_session(job_id)
        output = finish(system, user, session, mode=body.mode)
        stem = Path(session.job.filename).stem or "document"
        return FileResponse(
            output,
            media_type="application/pdf",
            filename=f"{stem}-filled.pdf",
        )

    @app.delete("/documents/{job_id}", status_code=204)
    async def discard_document(
        job_id: str, user: User = Depends(current_user)
    ) -> None:
        from .documents import discard

        session = app.state.documents.pop(job_id, None)
        if session is None:
            raise HTTPException(status_code=404, detail=f"no document job {job_id!r}")
        discard(session)

    return app


app = create_app  # convenience for ``uvicorn ecomops.api:app --factory``

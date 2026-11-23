"""ecomops CLI.

Commands:
    ecomops demo                full scenario: request + overstock report + delivery exception
    ecomops request TEXT        submit one customer request, print the trace
    ecomops exceptions          demo the delivery-exception path
    ecomops stock               run the proactive overstock clearance pass

Every command runs the system in-process, prints the event trace, and pauses on
any pending approval — you decide, the gate records it, the system continues.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .auth import Role, demo_user
from .documents import (
    DocumentError,
    answer as answer_document,
    discard as discard_document,
    finish as finish_document,
    make_workdir as document_workdir,
    message as document_message,
    open_document,
    rows as document_rows,
)
from .journal import JournalRecord, journal_summary, read_journal, verify_journal
from .system import EcomopsSystem, build_system

app = typer.Typer(add_completion=False, no_args_is_help=True, help="Event-driven e-commerce operations agents.")
doc_app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Document intake: read a PDF or image, fill it from the event store.",
)
app.add_typer(doc_app, name="doc")
console = Console()

SYSTEM_OPT = typer.Option(
    False, "--mlx", help="Use local MLX model (mlx-lm, default Qwen3-4B-4bit) with rule-based fallback"
)
MODEL_OPT = typer.Option(
    None, "--model", help="MLX model repo id, e.g. mlx-community/Qwen3-8B-4bit (implies --mlx)"
)
APPROVE_OPT = typer.Option("ask", "--approve", help="ask = pause on approvals; yes = grant all; no = deny all")
JOURNAL_OPT = typer.Option(
    None, "--journal", help="Append every event to this JSONL journal (audit/replay)"
)
GROCERY_OPT = typer.Option(
    None, "--grocery", metavar="PATH",
    help="Merge a grocery CSV (e.g. data/grocery_catalogue.csv) into the catalogue",
)
GROCERY_LIMIT_OPT = typer.Option(
    None, "--grocery-limit", min=0, help="Load at most N grocery products (default: all)"
)


def _new_system(
    use_llm: bool,
    model: str | None = None,
    journal: str | None = None,
    grocery: str | None = None,
    grocery_limit: int | None = None,
) -> EcomopsSystem:
    return build_system(
        use_mlx=use_llm or bool(model),
        model=model,
        journal_path=journal,
        grocery=grocery,
        grocery_limit=grocery_limit,
    )


def _catalogue_label(system: EcomopsSystem) -> str:
    total = len(system.inventory.products)
    report = system.grocery_report
    if report is None:
        return f"{total} demo SKUs"
    return f"{total} SKUs · {report.loaded} grocery ({report.path.name})"


def _journal_details(record: JournalRecord) -> str:
    body = record.event.model_dump(
        mode="json",
        exclude={"event_id", "correlation_id", "causation_id", "source", "ts"},
    )
    parts = []
    for key, value in body.items():
        if isinstance(value, dict):
            value = "{...}"
        elif isinstance(value, list):
            value = f"[{len(value)} items]" if value else "[]"
        parts.append(f"{key}={value}")
    return ", ".join(parts)


def _print_trace(system: EcomopsSystem) -> None:
    table = Table(title="Event trace", show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Topic", style="cyan")
    table.add_column("Source", style="magenta")
    table.add_column("Details", style="white")
    for seq, topic, source, summary in system.bus.trace():
        table.add_row(str(seq), topic, source, summary[:90])
    console.print(table)


def _print_approvals(system: EcomopsSystem) -> list:
    pending = system.pending_approvals
    if not pending:
        return []
    table = Table(title="[yellow]Pending approvals[/yellow]")
    table.add_column("Proposal", style="cyan")
    table.add_column("Action", style="white")
    table.add_column("Reason", style="white")
    table.add_column("Risk", style="red")
    for e in pending:
        table.add_row(e.proposal_id, e.action, e.reason[:60], e.risk.value)
    console.print(table)
    return pending


async def _settle(system: EcomopsSystem, mode: str) -> None:
    """Resolve pending approvals according to the --approve mode."""
    while True:
        pending = system.pending_approvals
        if not pending:
            return
        if mode == "yes":
            for e in pending:
                await system.decide(e.proposal_id, granted=True, decided_by="auto-approve")
        elif mode == "no":
            for e in pending:
                await system.decide(e.proposal_id, granted=False, decided_by="auto-deny")
        else:  # ask
            for e in pending:
                grant = typer.confirm(f"  Approve [{e.risk.value}] {e.action}: {e.reason}?", default=True)
                await system.decide(e.proposal_id, granted=grant, decided_by="operator")
        # loop once more: a decision may itself unblock more work
        if not system.pending_approvals:
            return


def _summary_panel(system: EcomopsSystem) -> None:
    inv = system.inventory
    routed = system.bus.of_type("ShipmentRouted")
    resolved = system.bus.of_type("DeliveryExceptionResolved")
    reserved = system.bus.of_type("StockReserved")
    discounts = system.bus.of_type("DiscountApplied")
    refunds = system.bus.of_type("RefundIssued")
    replacements = system.bus.of_type("ReplacementAuthorised")
    lines = [
        f"reservations: {len(reserved)}   shipments routed: {len(routed)}   "
        f"discounts applied: {len(discounts)}   exceptions resolved: {len(resolved)}   "
        f"refunds: {len(refunds)}   replacements: {len(replacements)}",
        "",
        "final prices: "
        + ", ".join(f"{p.sku}=${p.unit_price:.2f}" for p in list(inv.products.values())[:4]),
    ]
    console.print(Panel("\n".join(lines), title="Outcome", border_style="green"))


async def _demo(system: EcomopsSystem, approve_mode: str) -> None:
    console.rule("[bold]1. Customer request")
    await system.receive_customer_request(
        "CUST-001",
        "Hi, I need 2 pairs of wireless bluetooth earbuds delivered to Berlin ASAP, "
        "and do you have an aluminium laptop stand in stock? Also the noise cancelling headphones.",
    )

    console.rule("[bold]2. Warehouse stock report (proactive clearance)")
    await system.report_stock_levels()

    _print_approvals(system)
    await _settle(system, approve_mode)

    console.rule("[bold]3. Delivery exception arrives from the carrier")
    routed = system.bus.of_type("ShipmentRouted")
    if routed:
        await system.report_delivery_exception(routed[0].shipment_id, "carrier_delay", "hub strike in transit")
        _print_approvals(system)
        await _settle(system, approve_mode)

    console.rule("[bold]4. Customer returns a delivered item")
    await system.receive_customer_request(
        "CUST-001",
        "I want a refund for the wireless bluetooth earbuds — they arrived damaged",
    )
    _print_approvals(system)
    await _settle(system, approve_mode)

    _print_trace(system)
    _summary_panel(system)


@app.command()
def demo(
    llm: bool = SYSTEM_OPT,
    model: str | None = MODEL_OPT,
    approve: str = APPROVE_OPT,
    journal: str | None = JOURNAL_OPT,
    grocery: str | None = GROCERY_OPT,
    grocery_limit: int | None = GROCERY_LIMIT_OPT,
) -> None:
    """Run the full scenario end to end."""

    async def main() -> None:
        system = _new_system(llm, model, journal, grocery, grocery_limit)
        use_real = llm or bool(model)
        console.print(
            Panel(
                f"LLM backend: {'mlx + rule fallback' if use_real else 'rule-based (deterministic)'}"
                f"\nApproval mode: {approve}"
                f"\nPolicy: discount≤{system.policy.max_autonomous_discount_pct}%  "
                f"qty≤{system.policy.max_autonomous_reservation_qty}  "
                f"value≤${system.policy.max_autonomous_action_value}"
                f"\nCatalogue: {_catalogue_label(system)}",
                title="ecomops demo",
            )
        )
        await _demo(system, approve)

    asyncio.run(main())


@app.command()
def request(
    text: str,
    customer: str = typer.Option("CUST-001", "--customer", "-c"),
    llm: bool = SYSTEM_OPT,
    model: str | None = MODEL_OPT,
    approve: str = APPROVE_OPT,
    journal: str | None = JOURNAL_OPT,
    grocery: str | None = GROCERY_OPT,
    grocery_limit: int | None = GROCERY_LIMIT_OPT,
) -> None:
    """Submit a single customer request through the whole pipeline."""

    async def main() -> None:
        system = _new_system(llm, model, journal, grocery, grocery_limit)
        await system.receive_customer_request(customer, text)
        _print_approvals(system)
        await _settle(system, approve)
        _print_trace(system)
        _summary_panel(system)

    asyncio.run(main())


@app.command(name="exceptions")
def exceptions_cmd(
    llm: bool = SYSTEM_OPT,
    model: str | None = MODEL_OPT,
    approve: str = APPROVE_OPT,
    journal: str | None = JOURNAL_OPT,
    grocery: str | None = GROCERY_OPT,
    grocery_limit: int | None = GROCERY_LIMIT_OPT,
) -> None:
    """Exercise only the transit-exception path (needs a routed shipment first)."""

    async def main() -> None:
        system = _new_system(llm, model, journal, grocery, grocery_limit)
        await system.receive_customer_request("CUST-002", "1 mechanical keyboard to Paris, no rush")
        await _settle(system, approve)
        routed = system.bus.of_type("ShipmentRouted")
        if not routed:
            console.print("[red]no shipment was routed — see trace above[/red]")
            _print_trace(system)
            return
        console.rule("[bold]carrier reports: parcel damaged in transit")
        await system.report_delivery_exception(routed[0].shipment_id, "damaged", "crushed box, unit unusable")
        _print_approvals(system)
        await _settle(system, approve)
        _print_trace(system)
        _summary_panel(system)

    asyncio.run(main())


@app.command(name="stock")
def stock_cmd(
    llm: bool = SYSTEM_OPT,
    model: str | None = MODEL_OPT,
    approve: str = APPROVE_OPT,
    journal: str | None = JOURNAL_OPT,
    grocery: str | None = GROCERY_OPT,
    grocery_limit: int | None = GROCERY_LIMIT_OPT,
) -> None:
    """Run the proactive overstock-clearance pass on its own."""

    async def main() -> None:
        system = _new_system(llm, model, journal, grocery, grocery_limit)
        await system.report_stock_levels()
        _print_approvals(system)
        await _settle(system, approve)
        _print_trace(system)
        _summary_panel(system)

    asyncio.run(main())


@app.command(name="returns")
def returns_cmd(
    llm: bool = SYSTEM_OPT,
    model: str | None = MODEL_OPT,
    approve: str = APPROVE_OPT,
    journal: str | None = JOURNAL_OPT,
    grocery: str | None = GROCERY_OPT,
    grocery_limit: int | None = GROCERY_LIMIT_OPT,
) -> None:
    """Buy something, then exercise the return / refund path."""

    async def main() -> None:
        system = _new_system(llm, model, journal, grocery, grocery_limit)
        await system.receive_customer_request("CUST-RET-001", "1 wireless bluetooth earbuds to Berlin")
        _print_approvals(system)
        await _settle(system, approve)

        routed = system.bus.of_type("ShipmentRouted")
        if not routed:
            console.print("[red]no shipment was routed — see trace above[/red]")
            _print_trace(system)
            return

        console.rule("[bold]customer reports the delivered item arrived damaged")
        await system.receive_customer_request(
            "CUST-RET-001",
            "I want a refund for the wireless bluetooth earbuds — they arrived damaged",
        )
        _print_approvals(system)
        await _settle(system, approve)
        _print_trace(system)
        _summary_panel(system)

    asyncio.run(main())


@app.command()
def catalogue(
    grocery: str | None = GROCERY_OPT,
    grocery_limit: int | None = GROCERY_LIMIT_OPT,
    search: str | None = typer.Option(
        None, "--search", "-s", help="Filter by product name (case-insensitive)"
    ),
    category: str | None = typer.Option(None, "--category", "-c", help="Filter by sub-category"),
    match: str | None = typer.Option(
        None, "--match", "-m", help="Show the catalogue's best match for a free-text query"
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="Rows to print"),
) -> None:
    """Inspect the merged catalogue and the matcher (interface-test helper)."""
    system = _new_system(False, None, None, grocery, grocery_limit)
    report = system.grocery_report
    if report is not None:
        console.print(Panel(report.label, title="Grocery pack"))

    if match:
        product, score = system.inventory.match(match)
        if product is None:
            console.print(
                f"[yellow]no match for {match!r} — best score {score:.2f} "
                f"(threshold {system.customer.match_threshold:.2f})[/yellow]"
            )
            raise typer.Exit(1)
        console.print(
            f"[green]match[/green] {product.sku} · {product.name} · "
            f"{product.category} · ${product.unit_price:.2f} · score {score:.2f}"
        )
        return

    products = list(system.inventory.products.values())
    if category:
        products = [p for p in products if category.casefold() in p.category.casefold()]
    if search:
        needle = search.casefold()
        products = [p for p in products if needle in p.name.casefold()]

    table = Table(
        title=f"Catalogue · {len(products)} matching / {len(system.inventory.products)} total"
    )
    for column, justify in (
        ("SKU", "left"), ("Product", "left"), ("Category", "left"),
        ("Price", "right"), ("Stock", "right"), ("Age", "right"), ("Rating", "right"),
    ):
        table.add_column(column, justify=justify)
    for p in products[:limit]:
        table.add_row(
            p.sku,
            p.name[:64] + ("…" if len(p.name) > 64 else ""),
            p.category,
            f"${p.unit_price:.2f}",
            str(p.stock),
            f"{p.age_days}d",
            f"{p.rating:.1f} ({p.reviews})" if p.rating is not None else "—",
        )
    console.print(table)
    if len(products) > limit:
        console.print(f"[dim]… {len(products) - limit} more — raise --limit to see them[/dim]")


@app.command()
def replay(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="Journal JSONL file"),
    topic: list[str] | None = typer.Option(None, "--topic", "-t", help="Only show these event topics"),
    correlation: str | None = typer.Option(None, "--correlation", "-c", help="Only show one conversation"),
    verify: bool = typer.Option(False, "--verify", help="Check the correlation/causation chain"),
) -> None:
    """Read a journal back (audit only — no side effects are re-executed)."""
    records = read_journal(path)
    if not records:
        console.print(f"[yellow]{path} contains no events.[/yellow]")
        raise typer.Exit(1)

    summary = journal_summary(records)
    console.print(
        Panel(
            f"events: {summary['events']}   conversations: {summary['conversations']}\n"
            f"first: {summary['first']}\nlast:  {summary['last']}",
            title=f"Journal · {path}",
        )
    )

    if verify:
        problems = verify_journal(records)
        if problems:
            for problem in problems:
                console.print(f"[red]✗ {problem}[/red]")
            raise typer.Exit(1)
        console.print("[green]✓ causal chain verified[/green]")

    selected = records
    if topic:
        selected = [r for r in selected if r.topic in set(topic)]
    if correlation:
        selected = [
            r for r in selected
            if (r.event.correlation_id or "").startswith(correlation)
        ]

    table = Table(title="Event replay", show_lines=False)
    table.add_column("#", justify="right", style="dim")
    table.add_column("Time", style="dim")
    table.add_column("Topic", style="cyan")
    table.add_column("Source", style="magenta")
    table.add_column("Details", style="white")
    for record in selected:
        table.add_row(
            str(record.seq),
            record.event.ts.strftime("%H:%M:%S"),
            record.topic,
            record.event.source,
            _journal_details(record)[:90],
        )
    console.print(table)


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Address to bind"),
    port: int = typer.Option(8000, "--port", "-p", help="Port to serve on"),
    llm: bool = SYSTEM_OPT,
    model: str | None = MODEL_OPT,
    journal: str | None = JOURNAL_OPT,
    grocery: str | None = GROCERY_OPT,
    grocery_limit: int | None = GROCERY_LIMIT_OPT,
    no_auth: bool = typer.Option(
        False, "--no-auth", help="Disable bearer-token auth (local demos only)."
    ),
) -> None:
    """Run the HTTP control plane (requires: pip install 'ecomops\\[api]')."""
    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]fastapi/uvicorn are not installed.[/red] "
            "Run: [bold]pip install -e '.[api]'[/bold]"
        )
        raise typer.Exit(1) from None

    from .api import create_app

    system = _new_system(llm, model, journal, grocery, grocery_limit)
    console.print(
        Panel(
            f"HTTP API on http://{host}:{port}\n"
            f"docs: http://{host}:{port}/docs\n"
            f"auth: {'disabled' if no_auth else 'bearer token'}\n"
            f"backend: {getattr(system.llm, 'backend_name', 'rule-based')}",
            title="MSM serve",
        )
    )
    uvicorn.run(create_app(system, require_auth=not no_auth), host=host, port=port)


@app.command()
def ui(
    port: int = typer.Option(8501, "--port", "-p", help="Port to serve the UI on"),
    host: str = typer.Option("127.0.0.1", "--host", help="Address to bind"),
    grocery: str | None = GROCERY_OPT,
    grocery_limit: int | None = GROCERY_LIMIT_OPT,
    login: str | None = typer.Option(
        None,
        "--login",
        help="Skip the gate as a demo role (md|supervisor|accountant|staff). Dev mode only.",
    ),
) -> None:
    """Launch the MSM console (requires: pip install 'ecomops\\[ui]')."""
    import os
    import sys
    from pathlib import Path

    try:
        from streamlit.web import cli as stcli
    except ImportError:
        console.print("[red]streamlit is not installed.[/red] Run: [bold]uv pip install -e '.[ui]'[/bold]")
        raise typer.Exit(1) from None

    # Streamlit runs app.py in its own process, so the catalogue choice travels
    # through the environment (the app reads it on every system build).
    if grocery:
        os.environ["ECOMOPS_GROCERY"] = str(Path(grocery).expanduser().resolve())
    if grocery_limit is not None:
        os.environ["ECOMOPS_GROCERY_LIMIT"] = str(grocery_limit)
    if login:
        os.environ["ECOMOPS_AUTH_AUTOLOGIN"] = login.strip().lower()

    app_path = Path(__file__).with_name("app.py")
    os.environ.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    sys.argv = [
        "streamlit", "run", str(app_path),
        "--server.address", host,
        "--server.port", str(port),
        "--theme.base", "dark",
        "--theme.primaryColor", "#c8a96a",
        "--theme.backgroundColor", "#0b0b0d",
        "--theme.secondaryBackgroundColor", "#141417",
        "--theme.textColor", "#f2efe8",
    ]
    stcli.main()


# --------------------------------------------------------------------------- #
# Document intake
# --------------------------------------------------------------------------- #

def _system_for_documents(llm: bool, grocery: str | None, grocery_limit: int | None) -> EcomopsSystem:
    return _new_system(llm, None, None, grocery, grocery_limit)


def _print_document(session) -> None:
    console.print(
        Panel(
            document_message(session).replace("**", ""),
            title=session.job.doc_type.label,
            subtitle=f"{session.job.filename} · sha {session.job.sha256[:10]}",
        )
    )
    table = Table("Field", "Document", "MSM", "Source", "Status")
    for row in document_rows(session.job):
        table.add_row(
            row["field"], row["document"] or "—", row["msm"] or "—", row["source"] or "—", row["status"]
        )
    console.print(table)


@doc_app.command("extract")
def doc_extract(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="PDF, PNG or JPEG"),
    llm: bool = SYSTEM_OPT,
    grocery: str | None = GROCERY_OPT,
    grocery_limit: int | None = GROCERY_LIMIT_OPT,
) -> None:
    """Read a document, resolve its fields, and print what is still missing."""
    system = _system_for_documents(llm, grocery, grocery_limit)
    workdir = document_workdir()
    try:
        session = open_document(system, demo_user(Role.MD), file, workdir=workdir)
    except DocumentError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    try:
        _print_document(session)
    finally:
        discard_document(session)


@doc_app.command("fill")
def doc_fill(
    file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True, help="PDF, PNG or JPEG"),
    out: Path | None = typer.Option(None, "--out", "-o", help="Output PDF (default: <name>-filled.pdf)"),
    mode: str = typer.Option("original", "--mode", help="original (keep geometry) or reflow (clean rebuild)"),
    answers: list[str] = typer.Option([], "--answer", "-a", help="key=value, repeatable (e.g. sku=SKU-1001)"),
    llm: bool = SYSTEM_OPT,
    grocery: str | None = GROCERY_OPT,
    grocery_limit: int | None = GROCERY_LIMIT_OPT,
) -> None:
    """Fill a document and write a PDF; unanswered fields are left blank."""
    if mode not in {"original", "reflow"}:
        console.print("[red]--mode must be original or reflow[/red]")
        raise typer.Exit(1)
    system = _system_for_documents(llm, grocery, grocery_limit)
    workdir = document_workdir()
    try:
        session = open_document(system, demo_user(Role.MD), file, workdir=workdir)
    except DocumentError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    try:
        if answers:
            answer_document(system, demo_user(Role.MD), session, "\n".join(answers))
        target = out or file.with_name(f"{file.stem}-filled.pdf")
        output = finish_document(system, demo_user(Role.MD), session, output=target, mode=mode)
        console.print(f"[green]Wrote[/green] {output} · mode {mode}")
        _print_document(session)
    finally:
        discard_document(session)


if __name__ == "__main__":
    app()

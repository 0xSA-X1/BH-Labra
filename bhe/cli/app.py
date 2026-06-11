"""The ``bhe`` Typer application - curated read-only verbs + a generic ``get``.

Each command is a thin wrapper: resolve a profile, call one client coroutine via
:func:`bhe.cli._runtime.run`, render the result. Adding a new endpoint is adding
a ~3-line function here. Anything not yet wrapped is reachable today through
``bhe get <path>``.
"""

from __future__ import annotations

import getpass

import typer

from bhe import __version__
from bhe.cli._resolve import resolve_domain, resolve_principal
from bhe.cli._runtime import (
    console,
    err_console,
    output,
    print_json,
    resolve_profile,
    run,
    settings,
)
from bhe.cli.hunt import hunt_app
from bhe.config import KeyringUnavailable
from bhe.scope import (
    backward_reach,
    make_expander,
    rank_choke_points,
    seeds_from_response,
    tier_zero_seed_query,
)


async def _build_snapshot(client, *, target, tier_zero, max_depth, fanin, concurrency=8):
    """Resolve seeds (a target, or all of Tier Zero) and walk backward.

    Shared by ``choke`` / ``leaks`` / ``map`` — they're all views over the same
    Tier-Zero-reachable snapshot.
    """
    if tier_zero:
        seeds = seeds_from_response(await client.cypher_query(tier_zero_seed_query()))
    else:
        node = await resolve_principal(client, target)
        oid = node.get("objectid")
        seeds = {oid: (node.get("name", oid), node.get("type") or "", "")} if oid else {}
    if not seeds:
        return None
    return await backward_reach(
        seeds,
        make_expander(client, threshold=fanin, concurrency=concurrency),
        max_depth=max_depth,
    )


def scoped_snapshot(ctx, *, target, tier_zero, max_depth, fanin, refresh=False, concurrency=8):
    """Build (or reuse a cached) backward snapshot. Returns ``(snapshot, age)``.

    Mock mode never touches the on-disk cache (keeps tests/synthetic runs clean).
    """
    def _build():
        return run(
            ctx,
            lambda c: _build_snapshot(
                c, target=target, tier_zero=tier_zero, max_depth=max_depth,
                fanin=fanin, concurrency=concurrency,
            ),
        )

    profile = resolve_profile(settings(ctx))
    if profile.mock:
        return _build(), None

    from bhe.cache import load_snapshot, save_snapshot, snapshot_key

    key = snapshot_key(profile.name, target, tier_zero, max_depth, fanin)
    if not refresh:
        hit = load_snapshot(key)
        if hit is not None:
            return hit
    snapshot = _build()
    if snapshot is not None and snapshot.nodes:
        save_snapshot(key, snapshot)
    return snapshot, None


def _note_cache(ctx, cache_age) -> None:
    """Print a dim 'reused cache' note to stdout (human runs only)."""
    if cache_age is None or settings(ctx).as_json:
        return
    when = f"{cache_age / 60:.0f}m" if cache_age >= 60 else f"{cache_age:.0f}s"
    console.print(f"[dim]Reused cached snapshot ({when} old); --refresh to rebuild.[/dim]")

app = typer.Typer(
    name="bhe",
    no_args_is_help=True,
    add_completion=True,
    help="Ergonomic, read-only CLI for the BloodHound Enterprise API.",
    rich_markup_mode="rich",
)
app.add_typer(hunt_app, name="hunt")


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"bhe {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    ctx: typer.Context,
    profile: str = typer.Option(
        None, "--profile", "-p", help="Profile name from the YAML profiles file."
    ),
    mock: bool = typer.Option(
        False, "--mock", help="Serve bundled fixtures (no tenant/creds needed)."
    ),
    profiles_file: str = typer.Option(
        None, "--profiles-file", help="Path to a YAML profiles file."
    ),
    json_: bool = typer.Option(
        False, "--json", "-j", help="Emit raw JSON instead of tables."
    ),
    _version: bool = typer.Option(
        None,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the bhe CLI version and exit.",
    ),
) -> None:
    from bhe.cli._runtime import Settings

    ctx.obj = Settings(
        profile=profile, mock=mock, profiles_file=profiles_file, as_json=json_
    )


# ----------------------------------------------------------------------------
# Identity / tenant info
# ----------------------------------------------------------------------------


@app.command()
def self(ctx: typer.Context) -> None:
    """Show the authenticated identity and token role (GET /api/v2/self)."""
    output(ctx, run(ctx, lambda c: c.get_self()), title="Self")


@app.command("api-version")
def api_version(ctx: typer.Context) -> None:
    """Show the tenant's BHE API/server version (GET /api/version)."""
    output(ctx, run(ctx, lambda c: c.get_version()), title="API version")


@app.command()
def profile(ctx: typer.Context) -> None:
    """Show the active connection profile (redacted - never the token key)."""
    from bhe.config import load_profile

    s = settings(ctx)
    try:
        prof = load_profile(s.profile, mock=s.mock or None, profiles_file=s.profiles_file)
    except (KeyError, FileNotFoundError) as exc:
        err_console.print(f"[red]Config error:[/red] {exc}")
        raise typer.Exit(code=2)
    output(ctx, prof.redacted(), title="Active profile")


@app.command()
def info(ctx: typer.Context) -> None:
    """Show version, active profile, and where bhe stores logs/cache on this host."""
    import platform

    from bhe.cache import app_cache_dir
    from bhe.config import load_profile
    from bhe.observability import app_log_dir, log_file_for

    s = settings(ctx)
    try:
        prof = load_profile(s.profile, mock=s.mock or None, profiles_file=s.profiles_file)
    except (KeyError, FileNotFoundError):
        prof = None

    details = {
        "bhe version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "profile": prof.name if prof else "(unresolved)",
        "base_url": (prof.base_url or "(unset)") if prof else "",
        "mock": str(prof.mock) if prof else "",
        "token key": ("set" if prof and prof.token_key else "not set") if prof else "",
        "cache dir": str(app_cache_dir()),
        "log dir": str(app_log_dir()),
        "log file": str(log_file_for(prof.name)) if prof else "",
    }
    output(ctx, details, title="bhe")


# ----------------------------------------------------------------------------
# Domains / posture
# ----------------------------------------------------------------------------


@app.command()
def domains(
    ctx: typer.Context,
    selector: str = typer.Argument(
        None, help="Optional domain name/id to drill into (e.g. CORP.LOCAL)."
    ),
) -> None:
    """List domains, or drill into one by name/id to see a findings summary."""
    if selector is None:
        output(ctx, run(ctx, lambda c: c.get_available_domains()), title="Domains")
        return

    async def _detail(c):
        dom = await resolve_domain(c, selector)
        findings = await c.get_domain_attack_path_findings(dom["id"])
        return dom, findings

    dom, findings = run(ctx, _detail)
    rows = findings.get("data", []) if isinstance(findings, dict) else (findings or [])
    if settings(ctx).as_json:
        print_json({"domain": dom, "findings": rows})
        return

    from collections import Counter

    output(ctx, dom, title=f"Domain - {dom.get('name')}")
    counts = Counter(f.get("finding", "?") for f in rows)
    if counts:
        summary = [{"finding": k, "count": v} for k, v in counts.most_common()]
        output(ctx, summary, title="Findings summary")
    name = dom.get("name", selector)
    console.print(
        f"[dim]Drill further:[/dim] bhe findings {name}  |  "
        f"bhe hunt tier-zero <principal>  |  bhe hunt hybrid <principal>"
    )


@app.command()
def posture(ctx: typer.Context) -> None:
    """Show current risk-posture stats (GET /api/v2/posture-stats)."""
    output(ctx, run(ctx, lambda c: c.get_posture_stats()), title="Posture")


@app.command()
def findings(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Domain name or id (e.g. CORP.LOCAL)."),
) -> None:
    """List a domain's attack-path findings (resolves the domain by name)."""

    async def _go(c):
        dom = await resolve_domain(c, domain)
        return await c.get_domain_attack_path_findings(dom["id"])

    output(
        ctx,
        run(ctx, _go),
        columns=["finding", "principal", "principal_kind", "severity", "accepted", "exposure"],
        title=f"Findings - {domain}",
    )


@app.command("attack-paths")
def attack_paths(ctx: typer.Context) -> None:
    """List attack paths (GET /api/v2/attack-paths)."""
    output(ctx, run(ctx, lambda c: c.get_attack_paths()), title="Attack paths")


@app.command()
def triage(
    ctx: typer.Context,
    top: int = typer.Option(20, "--top", "-n", help="Show the top N rows."),
    severity: str = typer.Option(None, "--severity", help="Filter to a severity."),
    by_type: bool = typer.Option(
        False, "--by-type", help="Collapse domains; one row per finding type."
    ),
    include_accepted: bool = typer.Option(
        False, "--include-accepted", help="Also score accepted-risk findings."
    ),
    domain: str = typer.Option(
        None, "--domain", help="Limit to one domain (name or id)."
    ),
) -> None:
    """Rank attack-path findings across ALL domains -> what to fix first.

    Uses BHE's precomputed findings (no Cypher), so it stays fast on large estates.
    Score = severity_weight x active_principals x (1 + max_exposure).
    """
    from bhe.api.client import BHEClientError
    from bhe.triage import rollup_by_type, severity_rank, severity_totals, summarize

    async def _go(c):
        domains = [await resolve_domain(c, domain)] if domain else await c.get_available_domains()
        collected = []
        for d in domains:
            try:
                findings = await c.get_domain_attack_path_findings(d["id"])
            except BHEClientError:
                continue  # some domains 404 findings on older tenants — skip
            collected.append((d, findings))
        return collected

    rows = summarize(run(ctx, _go), include_accepted=include_accepted)
    if severity:
        rows = [r for r in rows if r.severity.lower() == severity.lower()]

    if by_type:
        output(
            ctx,
            rollup_by_type(rows)[:top],
            columns=["finding", "severity", "domains", "principals", "max_exposure", "score"],
            title="Triage - by finding type",
        )
        return

    if not settings(ctx).as_json:
        totals = severity_totals(rows)
        order = sorted(totals.items(), key=lambda kv: severity_rank(kv[0]), reverse=True)
        breakdown = "  ".join(f"{k}:{v}" for k, v in order) or "(none)"
        console.print(
            f"[bold]{len(rows)}[/bold] active finding-groups  |  "
            f"principals by severity: {breakdown}"
        )
    output(
        ctx,
        [r.as_dict() for r in rows[:top]],
        columns=["finding", "domain", "severity", "principals", "max_exposure", "score"],
        title="Triage - start here",
    )


# ----------------------------------------------------------------------------
# Collection: clients, jobs, schedules
# ----------------------------------------------------------------------------


@app.command()
def clients(ctx: typer.Context) -> None:
    """List registered collection clients (GET /api/v2/clients)."""
    output(ctx, run(ctx, lambda c: c.get_clients()), title="Clients")


@app.command()
def client(
    ctx: typer.Context, client_id: str = typer.Argument(..., help="Client id.")
) -> None:
    """Show a single collection client's detail."""
    output(ctx, run(ctx, lambda c: c.get_client(client_id)), title=f"Client {client_id}")


@app.command()
def jobs(
    ctx: typer.Context,
    current: bool = typer.Option(False, "--current", help="Only running jobs."),
    finished: bool = typer.Option(False, "--finished", help="Only finished jobs."),
) -> None:
    """List collection jobs, correlated to their client (name/host) so you never
    have to deal with raw client GUIDs."""

    async def _go(c):
        if current:
            data = await c.get_current_jobs()
        elif finished:
            data = await c.get_finished_jobs()
        else:
            data = await c.get_jobs()
        clients = await c.get_clients()
        return data, clients

    raw_jobs, clients = run(ctx, _go)
    by_id = {cl.get("id"): cl for cl in clients}
    rows = []
    for j in raw_jobs:
        cl = by_id.get(j.get("client_id"), {})
        rows.append(
            {
                "id": j.get("id"),
                "status": j.get("status"),
                "client": cl.get("name") or j.get("client_id", ""),
                "hostname": cl.get("hostname", ""),
                "start": j.get("start_time", ""),
                "end": j.get("end_time") or "-",
                "message": j.get("status_message", ""),
            }
        )
    output(
        ctx,
        rows,
        columns=["id", "status", "client", "hostname", "start", "end", "message"],
        title="Jobs",
    )


@app.command()
def job(
    ctx: typer.Context, job_id: str = typer.Argument(..., help="Job id.")
) -> None:
    """Show a single collection job's detail."""
    output(ctx, run(ctx, lambda c: c.get_job(job_id)), title=f"Job {job_id}")


@app.command()
def events(ctx: typer.Context) -> None:
    """List scheduled collection events / schedules (GET /api/v2/events)."""
    output(ctx, run(ctx, lambda c: c.get_events()), title="Schedules")


# ----------------------------------------------------------------------------
# Search / entities
# ----------------------------------------------------------------------------


@app.command()
def search(
    ctx: typer.Context,
    term: str = typer.Argument(..., help="Name fragment or object id."),
    kind: str = typer.Option(None, "--kind", "-k", help="Node kind, e.g. User."),
) -> None:
    """Search for nodes by name/objectid (GET /api/v2/search)."""
    output(ctx, run(ctx, lambda c: c.search(term, kind)), title=f"Search: {term}")


_ENTITY_GETTERS = {
    "user": "get_user",
    "computer": "get_computer",
    "group": "get_group",
    "domain": "get_domain",
    "gpo": "get_gpo",
}


@app.command()
def entity(
    ctx: typer.Context,
    selector: str = typer.Argument(..., help="Principal name or objectid."),
    kind: str = typer.Option(
        None, "--kind", "-k", help=f"Hint the kind: {', '.join(_ENTITY_GETTERS)}."
    ),
) -> None:
    """Show entity detail by NAME or objectid (resolves names; disambiguates)."""

    async def _go(c):
        node = await resolve_principal(c, selector, kind)
        node_kind = (kind or node.get("type") or "").lower()
        method = _ENTITY_GETTERS.get(node_kind)
        if method and node.get("objectid"):
            return await getattr(c, method)(node["objectid"])
        return node  # fall back to the resolved search record

    result = run(ctx, _go)
    if settings(ctx).as_json:
        print_json(result)
        return
    # Unwrap BHE's {"data": {"props": {...}}} entity envelope for a clean view.
    payload = result
    if (
        isinstance(payload, dict)
        and isinstance(payload.get("data"), dict)
        and "props" in payload["data"]
    ):
        payload = payload["data"]["props"]
    output(ctx, payload, title=f"Entity - {selector}")


# ----------------------------------------------------------------------------
# Cypher / pathfinding
# ----------------------------------------------------------------------------


@app.command()
def cypher(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Read-only Cypher (write clauses blocked)."),
) -> None:
    """Run a read-only Cypher query and show the returned nodes."""
    from bhe.parsing.graph import extract_literals, nodes_table

    result = run(ctx, lambda c: c.cypher_query(query))
    if settings(ctx).as_json:
        print_json(result)
        return
    rows = nodes_table(result)
    if rows:
        output(ctx, rows, title="Cypher nodes")
        return
    literals = extract_literals(result)
    if literals:
        output(ctx, [{"value": v} for v in literals], title="Cypher results")
    else:
        console.print("[dim](no nodes or literals returned)[/dim]")


# Pathfinding lives under `bhe hunt` (path / tier-zero / hybrid / ...).


# ----------------------------------------------------------------------------
# Backward scoping: choke points
# ----------------------------------------------------------------------------


@app.command()
def choke(
    ctx: typer.Context,
    target: str = typer.Argument(
        None, help="Principal name/id to funnel into. Omit when using --tier-zero."
    ),
    tier_zero: bool = typer.Option(
        False, "--tier-zero", "-z", help="Seed from ALL Tier Zero nodes."
    ),
    max_depth: int = typer.Option(4, "--max-depth", help="Backward hops to walk."),
    top: int = typer.Option(10, "--top", "-n", help="Max choke points to show."),
    fanin: int = typer.Option(
        50, "--fanin", help="Truncate nodes with more inbound edges than this."
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Ignore any cached snapshot."),
    concurrency: int = typer.Option(8, "--concurrency", help="Max parallel probe queries."),
) -> None:
    """Rank the choke points whose fix cuts the most attack paths into a target.

    Walks BACKWARD from the target (or all of Tier Zero) in bounded, batched hops
    so it never issues a global path query that would time out, then ranks the
    highest-leverage nodes to remediate first.
    """
    if not target and not tier_zero:
        err_console.print("[red]Give a target name/id, or pass --tier-zero.[/red]")
        raise typer.Exit(code=2)

    snapshot, cache_age = scoped_snapshot(
        ctx, target=target, tier_zero=tier_zero, max_depth=max_depth, fanin=fanin,
        refresh=refresh, concurrency=concurrency,
    )
    if snapshot is None or not snapshot.nodes:
        err_console.print("[yellow]No seeds resolved / nothing reaches the target.[/yellow]")
        raise typer.Exit(code=1)
    _note_cache(ctx, cache_age)

    chokes, baseline = rank_choke_points(snapshot, max_points=top)
    if settings(ctx).as_json:
        print_json(
            {
                "seeds": sorted(snapshot.seeds),
                "nodes_explored": len(snapshot.nodes),
                "rounds": snapshot.rounds,
                "exposed_sources": baseline,
                "truncated": snapshot.truncated,
                "choke_points": [
                    {
                        "objectid": cp.objectid,
                        "name": cp.name,
                        "kind": cp.kind,
                        "depth": cp.depth,
                        "principals_cut": cp.principals_cut,
                        "pct_cut": round(cp.pct_cut, 3),
                    }
                    for cp in chokes
                ],
            }
        )
        return

    cap = "  [yellow](node cap hit - narrow with a target or --max-depth)[/yellow]" if snapshot.hit_limit else ""
    console.print(
        f"Explored [bold]{len(snapshot.nodes)}[/bold] nodes in "
        f"{snapshot.rounds} backward hop(s); [bold]{baseline}[/bold] source(s) "
        f"reach the target set.{cap}"
    )
    if not chokes:
        console.print("[dim]No single-node choke points (paths are already disjoint).[/dim]")
        return
    rows = [
        {
            "rank": i + 1,
            "choke_point": cp.name,
            "kind": cp.kind,
            "depth": cp.depth,
            "principals_cut": cp.principals_cut,
            "pct": f"{cp.pct_cut * 100:.0f}%",
        }
        for i, cp in enumerate(chokes)
    ]
    output(
        ctx,
        rows,
        columns=["rank", "choke_point", "kind", "depth", "principals_cut", "pct"],
        title="Choke points - fix these first",
    )


@app.command()
def leaks(
    ctx: typer.Context,
    target: str = typer.Argument(
        None, help="Principal name/id to funnel into. Omit when using --tier-zero."
    ),
    tier_zero: bool = typer.Option(
        True, "--tier-zero/--no-tier-zero", "-z", help="Seed from ALL Tier Zero nodes."
    ),
    max_depth: int = typer.Option(4, "--max-depth", help="Backward hops to walk."),
    fanin: int = typer.Option(50, "--fanin", help="Mass-node truncation threshold."),
    refresh: bool = typer.Option(False, "--refresh", help="Ignore any cached snapshot."),
    concurrency: int = typer.Option(8, "--concurrency", help="Max parallel probe queries."),
) -> None:
    """Show cross-domain / cross-platform edges that leak into the target set.

    Surfaces where one domain (or Entra/Okta/etc.) reaches into another's crown
    jewels - the boundary crossings to prioritise.
    """
    if not target and not tier_zero:
        err_console.print("[red]Give a target name/id, or pass --tier-zero.[/red]")
        raise typer.Exit(code=2)

    snapshot, cache_age = scoped_snapshot(
        ctx, target=target, tier_zero=tier_zero, max_depth=max_depth, fanin=fanin,
        refresh=refresh, concurrency=concurrency,
    )
    if snapshot is None or not snapshot.nodes:
        err_console.print("[yellow]No seeds resolved / nothing reaches the target.[/yellow]")
        raise typer.Exit(code=1)
    _note_cache(ctx, cache_age)

    from collections import defaultdict

    agg: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"count": 0, "edges": set(), "into_t0": False}
    )
    for e in snapshot.edges:
        from_dom = snapshot.nodes[e.source].domain or "?"
        to_dom = snapshot.nodes[e.target].domain or "?"
        if from_dom == to_dom:
            continue
        bucket = agg[(from_dom, to_dom)]
        bucket["count"] += 1
        bucket["edges"].add(e.edge_type)
        bucket["into_t0"] = bucket["into_t0"] or e.target in snapshot.seeds

    rows = [
        {
            "from_domain": frm,
            "to_domain": to,
            "crossings": v["count"],
            "into_tier_zero": "yes" if v["into_t0"] else "no",
            "edges": ", ".join(sorted(v["edges"])),
        }
        for (frm, to), v in sorted(agg.items(), key=lambda kv: kv[1]["count"], reverse=True)
    ]
    if settings(ctx).as_json:
        print_json(rows)
        return
    if not rows:
        console.print("[green]No cross-domain leakage into the reachable set.[/green]")
        return
    output(
        ctx,
        rows,
        columns=["from_domain", "to_domain", "crossings", "into_tier_zero", "edges"],
        title="Cross-domain leakage",
    )


@app.command()
def map(
    ctx: typer.Context,
    target: str = typer.Argument(
        None, help="Principal name/id to funnel into. Omit when using --tier-zero."
    ),
    tier_zero: bool = typer.Option(
        False, "--tier-zero", "-z", help="Seed from ALL Tier Zero nodes."
    ),
    fmt: str = typer.Option("mermaid", "--format", "-f", help="mermaid | dot"),
    max_depth: int = typer.Option(4, "--max-depth", help="Backward hops to walk."),
    fanin: int = typer.Option(50, "--fanin", help="Mass-node truncation threshold."),
    bundle: int = typer.Option(
        8, "--bundle", help="Collapse >= N leaf sources per node into one meta-node."
    ),
    refresh: bool = typer.Option(False, "--refresh", help="Ignore any cached snapshot."),
    concurrency: int = typer.Option(8, "--concurrency", help="Max parallel probe queries."),
) -> None:
    """Emit a condensed, bundled attack-graph (Mermaid/DOT) of the funnel into T0.

    Choke points and Tier Zero are highlighted; large leaf-source fan-ins collapse
    into ``(N principals)`` meta-nodes - the visual remediation plan.
    """
    from bhe.render import to_dot, to_mermaid

    if not target and not tier_zero:
        err_console.print("[red]Give a target name/id, or pass --tier-zero.[/red]")
        raise typer.Exit(code=2)

    snapshot, cache_age = scoped_snapshot(
        ctx, target=target, tier_zero=tier_zero, max_depth=max_depth, fanin=fanin,
        refresh=refresh, concurrency=concurrency,
    )
    if snapshot is None or not snapshot.nodes:
        err_console.print("[yellow]No seeds resolved / nothing reaches the target.[/yellow]")
        raise typer.Exit(code=1)
    if cache_age is not None:  # note on stderr so stdout stays pure for piping
        err_console.print(f"[dim]Reused cached snapshot ({cache_age / 60:.0f}m old).[/dim]")

    choke_ids = {cp.objectid for cp in rank_choke_points(snapshot)[0]}
    renderer = to_dot if fmt.lower() == "dot" else to_mermaid
    # Print raw (no Rich markup/wrapping) so it pastes cleanly into a viewer.
    print(renderer(snapshot, choke_ids, bundle))


# ----------------------------------------------------------------------------
# Generic escape hatch
# ----------------------------------------------------------------------------


@app.command()
def get(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="API path, e.g. /api/v2/jobs."),
    param: list[str] = typer.Option(
        None, "--param", "-P", help="Query param 'key=value' (repeatable)."
    ),
) -> None:
    """Read-only GET against any API path. Always emits JSON."""
    params: dict[str, str] = {}
    for item in param or []:
        if "=" not in item:
            err_console.print(f"[red]Bad --param '{item}'[/red] (expected key=value).")
            raise typer.Exit(code=2)
        key, value = item.split("=", 1)
        params[key] = value
    if not path.startswith("/"):
        path = "/" + path
    result = run(ctx, lambda c: c._request("GET", path, params=params or None))
    print_json(result)


# ----------------------------------------------------------------------------
# Diagnostics + support bundle
# ----------------------------------------------------------------------------


@app.command()
def doctor(
    ctx: typer.Context,
    strict: bool = typer.Option(False, "--strict", help="Exit non-zero on warnings too."),
) -> None:
    """Run the read-only tenant health battery; exits non-zero on failures."""
    from bhe.diagnostics import run_diagnostics

    profile = resolve_profile(settings(ctx))
    report = run(ctx, lambda c: run_diagnostics(c, profile))
    if settings(ctx).as_json:
        console.print_json(report.to_json())
    else:
        console.print(report.render_text())
    if report.has_failures or (strict and report.has_warnings):
        raise typer.Exit(code=1)


@app.command()
def bundle(
    ctx: typer.Context,
    out: str = typer.Option(None, "--out", help="Output .zip path."),
) -> None:
    """Write a support-bundle zip (diagnostics + request trace) for a ticket."""
    import asyncio
    import platform
    from datetime import datetime, timezone
    from pathlib import Path

    from bhe.api.client import BHEClient
    from bhe.bundle import write_bundle
    from bhe.diagnostics import run_diagnostics
    from bhe.observability import RequestLog, iter_log_files, log_file_for

    profile = resolve_profile(settings(ctx))
    request_log = RequestLog()

    async def _go():
        async with BHEClient.connect(
            profile.base_url,
            profile.token_id,
            profile.token_key or "",
            mock=profile.mock,
            request_log=request_log,
        ) as client:
            return await run_diagnostics(client, profile)

    report = asyncio.run(_go())
    safe_name = log_file_for(profile.name).stem
    out_path = Path(out) if out else Path.cwd() / f"bhe-bundle-{safe_name}.zip"
    write_bundle(
        out_path,
        app_meta={
            "tool": "bhe",
            "version": __version__,
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        profile_meta=profile.redacted(),
        doctor_json=report.to_json(),
        session_log=request_log,
        history_files=list(iter_log_files(profile.name)),
    )
    console.print(
        f"Wrote support bundle: [bold]{out_path}[/bold] "
        f"({out_path.stat().st_size:,} bytes)"
    )


# ----------------------------------------------------------------------------
# Keyring management
# ----------------------------------------------------------------------------

keyring_app = typer.Typer(no_args_is_help=True, help="Manage stored Token Keys.")
app.add_typer(keyring_app, name="keyring")


@keyring_app.command("set")
def keyring_set(profile: str = typer.Argument("default")) -> None:
    """Store a profile's Token Key in the OS keyring (prompts, no echo)."""
    from bhe.config import set_keyring_token

    key = getpass.getpass(f"Token Key for profile '{profile}': ").strip()
    if not key:
        err_console.print("Aborted: empty key.")
        raise typer.Exit(code=1)
    try:
        set_keyring_token(profile, key)
    except KeyringUnavailable as exc:
        err_console.print(
            f"[red]No usable OS keyring backend[/red] ({exc}). "
            f"Use BHE_TOKEN_KEY or a YAML 'token_key' instead."
        )
        raise typer.Exit(code=1)
    console.print(f"Stored Token Key for profile '{profile}'.")


@keyring_app.command("get")
def keyring_get(profile: str = typer.Argument("default")) -> None:
    """Report whether a Token Key is stored for a profile."""
    from bhe.config import get_keyring_token, keyring_available

    if not keyring_available():
        console.print(f"Profile '{profile}': no keyring backend on this host.")
        return
    stored = get_keyring_token(profile)
    console.print(f"Profile '{profile}': {'key present' if stored else 'no key stored'}.")


@keyring_app.command("delete")
def keyring_delete(profile: str = typer.Argument("default")) -> None:
    """Remove any stored Token Key for a profile."""
    from bhe.config import delete_keyring_token

    delete_keyring_token(profile)
    console.print(f"Removed any stored Token Key for profile '{profile}'.")


# ----------------------------------------------------------------------------
# Snapshot cache management
# ----------------------------------------------------------------------------

cache_app = typer.Typer(no_args_is_help=True, help="Manage the backward-scope snapshot cache.")
app.add_typer(cache_app, name="cache")


@cache_app.command("list")
def cache_list(ctx: typer.Context) -> None:
    """List cached snapshots (file, age, size)."""
    import time

    from bhe.cache import app_cache_dir

    cache_dir = app_cache_dir()
    rows = []
    if cache_dir.is_dir():
        for path in sorted(cache_dir.glob("*.json")):
            st = path.stat()
            rows.append(
                {
                    "file": path.name,
                    "age_min": round((time.time() - st.st_mtime) / 60, 1),
                    "kb": round(st.st_size / 1024, 1),
                }
            )
    if settings(ctx).as_json:
        print_json(rows)
        return
    if not rows:
        console.print(f"[dim]No cached snapshots in {cache_dir}.[/dim]")
        return
    output(ctx, rows, columns=["file", "age_min", "kb"], title=f"Snapshot cache ({cache_dir})")


@cache_app.command("clear")
def cache_clear() -> None:
    """Delete all cached snapshots."""
    from bhe.cache import app_cache_dir

    cache_dir = app_cache_dir()
    removed = 0
    if cache_dir.is_dir():
        for path in cache_dir.glob("*.json"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    console.print(f"Cleared {removed} cached snapshot(s).")

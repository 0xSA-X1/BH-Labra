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
    domain_of,
    make_expander,
    rank_choke_points,
    seeds_from_response,
    seeds_in_domain,
    tier_zero_seed_query,
)


async def _build_snapshot(client, *, target, tier_zero, max_depth, fanin, domain=None, concurrency=8):
    """Resolve seeds (a target, or all of Tier Zero) and walk backward.

    Shared by ``choke`` / ``leaks`` / ``map`` — they're all views over the same
    Tier-Zero-reachable snapshot.  ``domain`` narrows the Tier-Zero seed set to
    one domain so a team can work one domain at a time.
    """
    if tier_zero:
        seeds = seeds_from_response(await client.cypher_query(tier_zero_seed_query()))
        if domain:
            dom = await resolve_domain(client, domain)
            aliases = {str(dom.get("name", "")).lower(), str(dom.get("id", "")).lower()} - {""}
            seeds = seeds_in_domain(seeds, aliases)
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


def scoped_snapshot(ctx, *, target, tier_zero, max_depth, fanin, domain=None, refresh=False, concurrency=8):
    """Build (or reuse a cached) backward snapshot. Returns ``(snapshot, age)``.

    Mock mode never touches the on-disk cache (keeps tests/synthetic runs clean).
    """
    def _build():
        return run(
            ctx,
            lambda c: _build_snapshot(
                c, target=target, tier_zero=tier_zero, max_depth=max_depth,
                fanin=fanin, domain=domain, concurrency=concurrency,
            ),
        )

    profile = resolve_profile(settings(ctx))
    if profile.mock:
        return _build(), None

    from bhe.cache import load_snapshot, save_snapshot, snapshot_key

    key = snapshot_key(profile.name, target, tier_zero, max_depth, fanin, domain)
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
    """List domains, or drill into one by name/id to see its details."""
    if selector is None:
        output(ctx, run(ctx, lambda c: c.get_available_domains()), title="Domains")
        return

    dom = run(ctx, lambda c: resolve_domain(c, selector))
    if settings(ctx).as_json:
        print_json(dom)
        return

    output(ctx, dom, title=f"Domain - {dom.get('name')}")
    name = dom.get("name", selector)
    console.print(
        f"[dim]Drill further:[/dim] bhe findings {name}  |  "
        f"bhe hunt tier-zero <principal>  |  bhe hunt hybrid <principal>"
    )


def _posture_rows(response) -> list:
    """Unwrap the posture-stats payload into a flat list of rows."""
    if isinstance(response, dict):
        return response.get("data", []) or []
    return response or []


def _latest_posture_per_domain(rows: list) -> list:
    """Keep only the most recent snapshot per domain (posture-stats is historical)."""
    best: dict = {}
    for r in rows:
        sid = r.get("domain_sid")
        if sid is None:
            continue
        key = (r.get("created_at", ""), r.get("id", 0), r.get("asset_group_tag_id", 0))
        if sid not in best or key > best[sid][0]:
            best[sid] = (key, r)
    return [v[1] for v in best.values()]


@app.command()
def posture(
    ctx: typer.Context,
    domain: str = typer.Argument(
        None, help="Optional domain name/id to scope to (e.g. ESSOS.LOCAL)."
    ),
    history: bool = typer.Option(
        False, "--history", help="Show every snapshot, not just the latest per domain."
    ),
) -> None:
    """Risk-posture stats per domain (latest snapshot, ranked by exposure).

    Pass a domain to scope to just that one (work one domain at a time).
    """

    async def _go(c):
        dom = await resolve_domain(c, domain) if domain else None
        rows = _posture_rows(await c.get_posture_stats())
        names = {d.get("id"): d.get("name") for d in await c.get_available_domains()}
        return rows, names, dom

    rows, names, dom = run(ctx, _go)
    if dom is not None:
        rows = [r for r in rows if r.get("domain_sid") == dom.get("id")]
    if not history:
        rows = _latest_posture_per_domain(rows)
    out = [
        {
            "domain": names.get(r.get("domain_sid")) or r.get("domain_sid", ""),
            "exposure": r.get("exposure_index"),
            "tier_zero": r.get("tier_zero_count"),
            "critical": r.get("critical_risk_count"),
            "updated": r.get("updated_at") or r.get("created_at", ""),
        }
        for r in rows
    ]
    out.sort(
        key=lambda x: x["exposure"] if isinstance(x["exposure"], (int, float)) else -1,
        reverse=True,
    )
    scope = f" - {dom.get('name')}" if dom is not None else ""
    output(
        ctx,
        out,
        columns=["domain", "exposure", "tier_zero", "critical", "updated"],
        title=f"Posture{scope}" + ("" if history else " (latest per domain)"),
    )


def _finding_type_id(t) -> str:
    """Normalise an available-types entry (string or object) to its id."""
    if isinstance(t, str):
        return t
    if isinstance(t, dict):
        return t.get("id") or t.get("finding") or t.get("type") or ""
    return ""


@app.command()
def findings(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Domain name or id (e.g. CORP.LOCAL)."),
) -> None:
    """List a domain's attack-path findings, counted per finding type.

    BHE's findings endpoint is per finding-type, so this lists the domain's
    available types and counts the affected principals for each.
    """
    from bhe.api.client import BHEClientError

    async def _go(c):
        dom = await resolve_domain(c, domain)
        types = await c.get_domain_available_types(dom["id"])
        rows = []
        for t in types or []:
            tid = _finding_type_id(t)
            if not tid:
                continue
            try:
                resp = await c.get_domain_findings(dom["id"], params={"finding": tid, "limit": 1})
            except BHEClientError:
                continue
            if isinstance(resp, dict) and isinstance(resp.get("count"), int):
                count = resp["count"]  # the envelope total, not just this page
            else:
                data = resp.get("data", resp) if isinstance(resp, dict) else resp
                count = len(data) if isinstance(data, list) else 0
            rows.append({"finding": tid, "principals": count})
        rows.sort(key=lambda r: r["principals"], reverse=True)
        return rows

    output(ctx, run(ctx, _go), columns=["finding", "principals"], title=f"Findings - {domain}")


async def _domain_finding_stats(client, dom, sem) -> tuple:
    """Per finding-type aggregates for one domain, from ``/details`` envelopes.

    One cheap ``?finding=<type>&limit=1`` call per type: the envelope ``count`` is
    the affected-principal total and the single record gives the type's
    ``Severity`` / ``ImpactPercentage`` (no need to page the big ``Props`` blobs).
    Every query is isolated and counted, so a domain or finding type that errors
    (Azure/Entra, uncollected, a 500) is skipped, never aborting the estate sweep.
    Returns ``([FindingStat, ...], n_failed)``.
    """
    import asyncio

    from bhe.triage import FindingStat

    did = dom.get("id")
    name = dom.get("name") or did or "?"
    if not did:
        return [], 0
    try:
        types = await client.get_domain_available_types(did)
    except Exception:  # noqa: BLE001 - one bad domain shouldn't sink the estate view
        return [], 1
    tids = [t for t in (_finding_type_id(x) for x in (types or [])) if t]
    failures = 0

    async def _one(tid: str):
        nonlocal failures
        async with sem:
            try:
                resp = await client.get_domain_findings(did, params={"finding": tid, "limit": 1})
            except Exception:  # noqa: BLE001 - skip and count this finding type
                failures += 1
                return None
        if not isinstance(resp, dict):
            return None
        data = resp.get("data") or []
        count = resp.get("count")
        principals = count if isinstance(count, int) else len(data)
        if principals <= 0:
            return None
        sample = data[0] if data else {}
        return FindingStat(
            finding=tid,
            domain=name,
            severity=str(sample.get("Severity") or "").lower(),
            principals=principals,
            impact=float(sample.get("ImpactPercentage") or 0.0),
        )

    results = await asyncio.gather(*(_one(t) for t in tids))
    return [s for s in results if s is not None], failures


@app.command()
def triage(
    ctx: typer.Context,
    domain: str = typer.Option(
        None, "--domain", "-d",
        help="Scope to one domain (name or id); omit for the whole estate.",
    ),
    top: int = typer.Option(20, "--top", "-n", help="Show the top N rows."),
    by_type: bool = typer.Option(
        False, "--by-type", help="Roll up across domains: one row per finding type."
    ),
) -> None:
    """Rank attack-path findings across the estate (or one domain) -> where to start.

    Unlike the website's per-domain Attack Paths view, this ranks every finding
    type across all domains in one list, scored transparently as severity-weight x
    affected principals x (1 + impact). ``--domain`` scopes to one domain;
    ``--by-type`` rolls up estate-wide.
    """
    import asyncio

    from bhe.triage import rank, rollup_by_type, severity_totals

    async def _go(c):
        domains = [await resolve_domain(c, domain)] if domain else await c.get_available_domains()
        sem = asyncio.Semaphore(8)  # bound fan-out so we don't trip the rate limiter
        results = await asyncio.gather(
            *(_domain_finding_stats(c, d, sem) for d in domains),
            return_exceptions=True,
        )
        stats, failures = [], 0
        for res in results:
            if isinstance(res, Exception):
                failures += 1
                continue
            domain_stats, errs = res
            stats.extend(domain_stats)
            failures += errs
        return stats, failures

    stats, failures = run(ctx, _go)

    if by_type:
        out = rollup_by_type(stats)[:top]
        columns = ["finding", "severity", "domains", "principals", "impact", "score"]
    else:
        out = [s.as_dict() for s in rank(stats)[:top]]
        columns = ["finding", "domain", "severity", "principals", "impact", "score"]

    def _skipped_note() -> None:
        if failures:
            console.print(
                f"[dim]{failures} finding query(ies) failed and were skipped "
                "(often Azure/Entra or uncollected domains).[/dim]"
            )

    if settings(ctx).as_json:
        print_json(out)
        return
    if not out:
        console.print("[green]No unremediated attack-path findings in scope.[/green]")
        _skipped_note()
        return
    totals = severity_totals(stats)
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(totals.items()))
    console.print(f"[dim]Affected principals by severity -> {summary}[/dim]")
    where = f"in {domain}" if domain else "across the estate"
    output(ctx, out, columns=columns, title=f"Triage - findings {where} (fix these first)")
    _skipped_note()


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


# Collection-flag field -> short label, in a sensible display order.
_COLLECTION_LABELS = {
    "session_collection": "session",
    "local_group_collection": "local-groups",
    "ad_structure_collection": "ad",
    "cert_services_collection": "cert-services",
    "ca_registry_collection": "ca-registry",
}


def _humanize_rrule(rrule: str) -> str:
    """Turn an iCal RRULE (e.g. ``FREQ=DAILY;INTERVAL=1``) into plain English."""
    if not rrule:
        return ""
    parts: dict[str, str] = {}
    for line in rrule.replace("\\n", "\n").splitlines():
        line = line.strip()
        if line.upper().startswith("DTSTART"):
            continue
        if line.upper().startswith("RRULE") and ":" in line:
            line = line.split(":", 1)[1]
        for kv in line.split(";"):
            if "=" in kv:
                key, value = kv.split("=", 1)
                parts[key.strip().upper()] = value.strip().upper()
    unit = {
        "MINUTELY": "minute", "HOURLY": "hour", "DAILY": "day",
        "WEEKLY": "week", "MONTHLY": "month", "YEARLY": "year",
    }.get(parts.get("FREQ", ""))
    if unit is None:
        return rrule  # unknown shape - show it raw rather than lie
    try:
        n = int(parts.get("INTERVAL", "1"))
    except ValueError:
        n = 1
    if n == 1:
        human = {"minute": "Every minute", "hour": "Hourly", "day": "Daily",
                 "week": "Weekly", "month": "Monthly", "year": "Yearly"}[unit]
    else:
        human = f"Every {n} {unit}s"
    if parts.get("FREQ") == "WEEKLY" and parts.get("BYDAY"):
        human += f" on {parts['BYDAY']}"
    return human


def _to_local(timestamp: str) -> str:
    """Render an RFC3339/UTC timestamp in the operator's local timezone.

    Uses the short zone abbreviation when the OS provides one (e.g. ``EDT`` on
    macOS/Linux) and falls back to a numeric offset (e.g. ``-04:00`` on Windows,
    where ``%Z`` would otherwise expand to "Eastern Daylight Time").
    """
    from datetime import datetime, timezone

    if not timestamp:
        return ""
    try:
        dt = datetime.fromisoformat(timestamp.strip().replace("Z", "+00:00"))
    except ValueError:
        return timestamp  # unparseable - better to show the raw value than nothing
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone()
    name = local.tzname() or ""
    if not name or " " in name or len(name) > 5:
        offset = local.strftime("%z")  # -0400
        name = (offset[:3] + ":" + offset[3:]) if offset else ""
    return f"{local:%Y-%m-%d %H:%M} {name}".strip()


def _collection_summary(event: dict) -> str:
    """Compact list of which data types a schedule collects (or ``(none)``)."""
    enabled = [label for key, label in _COLLECTION_LABELS.items() if event.get(key)]
    return ", ".join(enabled) if enabled else "(none)"


@app.command()
def events(ctx: typer.Context) -> None:
    """List collection schedules: when each client next collects, and what.

    This is the *schedule* - the recurrence and which data each run gathers. It's
    distinct from [bold]bhe jobs[/bold], which lists the actual collection *runs*
    and their status. Times are shown in your local timezone.
    """

    async def _go(c):
        return await c.get_events(), await c.get_clients()

    raw_events, clients = run(ctx, _go)
    by_id = {cl.get("id"): cl for cl in clients}
    rows = [
        {
            "id": ev.get("id"),
            "client": by_id.get(ev.get("client_id"), {}).get("name") or ev.get("client_id", ""),
            "hostname": by_id.get(ev.get("client_id"), {}).get("hostname", ""),
            "cadence": _humanize_rrule(ev.get("rrule", "")),
            "next_run": _to_local(ev.get("next_scheduled_at", "")),
            "collects": _collection_summary(ev),
        }
        for ev in raw_events
    ]
    output(
        ctx,
        rows,
        columns=["id", "client", "hostname", "cadence", "next_run", "collects"],
        title="Schedules",
    )


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
    domain: str = typer.Option(
        None, "--domain", "-d",
        help="Scope the Tier-Zero seeds to one domain (name or id). Implies --tier-zero.",
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
    highest-leverage nodes to remediate first.  Pass ``--domain`` to funnel into a
    single domain's Tier Zero (work one domain at a time).
    """
    if domain and not target:
        tier_zero = True  # a domain scope is meaningless without a Tier-Zero seed
    if not target and not tier_zero:
        err_console.print("[red]Give a target name/id, or pass --tier-zero / --domain.[/red]")
        raise typer.Exit(code=2)

    snapshot, cache_age = scoped_snapshot(
        ctx, target=target, tier_zero=tier_zero, max_depth=max_depth, fanin=fanin,
        domain=domain, refresh=refresh, concurrency=concurrency,
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
    domain: str = typer.Option(
        None, "--domain", "-d",
        help="Scope the Tier-Zero seeds to one domain (name or id) - leaks INTO it.",
    ),
    max_depth: int = typer.Option(4, "--max-depth", help="Backward hops to walk."),
    fanin: int = typer.Option(50, "--fanin", help="Mass-node truncation threshold."),
    refresh: bool = typer.Option(False, "--refresh", help="Ignore any cached snapshot."),
    concurrency: int = typer.Option(8, "--concurrency", help="Max parallel probe queries."),
) -> None:
    """Show cross-domain / cross-platform edges that leak into the target set.

    Surfaces where one domain (or Entra/Okta/etc.) reaches into another's crown
    jewels - the boundary crossings to prioritise.  ``--domain`` narrows it to the
    crossings that leak into one domain's Tier Zero.
    """
    if not target and not tier_zero:
        err_console.print("[red]Give a target name/id, or pass --tier-zero.[/red]")
        raise typer.Exit(code=2)

    snapshot, cache_age = scoped_snapshot(
        ctx, target=target, tier_zero=tier_zero, max_depth=max_depth, fanin=fanin,
        domain=domain, refresh=refresh, concurrency=concurrency,
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
        src = snapshot.nodes[e.source]
        tgt = snapshot.nodes[e.target]
        from_dom = domain_of(src.name, src.kind, src.domain)
        to_dom = domain_of(tgt.name, tgt.kind, tgt.domain)
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
    console.print(
        "[dim]into_tier_zero = the crossing lands directly on a Tier Zero asset; "
        '"no" rows still reach Tier Zero, but via an intermediary. '
        "(unknown) = a node BHE left without a domain.[/dim]"
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
    domain: str = typer.Option(
        None, "--domain", "-d",
        help="Scope the Tier-Zero seeds to one domain (name or id). Implies --tier-zero.",
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
    into ``(N principals)`` meta-nodes - the visual remediation plan.  ``--domain``
    scopes the funnel to one domain's Tier Zero.
    """
    from bhe.render import to_dot, to_mermaid

    if domain and not target:
        tier_zero = True  # a domain scope is meaningless without a Tier-Zero seed
    if not target and not tier_zero:
        err_console.print("[red]Give a target name/id, or pass --tier-zero / --domain.[/red]")
        raise typer.Exit(code=2)

    snapshot, cache_age = scoped_snapshot(
        ctx, target=target, tier_zero=tier_zero, max_depth=max_depth, fanin=fanin,
        domain=domain, refresh=refresh, concurrency=concurrency,
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

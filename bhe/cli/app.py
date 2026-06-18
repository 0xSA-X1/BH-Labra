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
    choke_targets,
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


def _as_percent(value) -> str:
    """Render a 0-1 exposure index as a whole percentage, matching the BHE web GUI."""
    if not isinstance(value, (int, float)):
        return ""
    return f"{value * 100:.0f}%"


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


def _posture_explain(ctx: typer.Context, domain: str) -> None:
    """Break down what drives one domain's exposure: its attack-path findings.

    BHE computes the exposure index server-side, so this doesn't recompute the
    number - it shows the findings that feed it (severity, affected principals,
    and BHE's per-finding impact), ranked by contribution.
    """
    import asyncio

    from bhe.triage import severity_rank

    async def _go(c):
        dom = await resolve_domain(c, domain)
        latest = _latest_posture_per_domain(_posture_rows(await c.get_posture_stats()))
        headline = next((r for r in latest if r.get("domain_sid") == dom.get("id")), None)
        stats, failures = await _domain_finding_stats(c, dom, asyncio.Semaphore(8))
        return dom, headline, stats, failures

    dom, headline, stats, failures = run(ctx, _go)
    name = dom.get("name") or domain
    # Rank by the real ExposurePercentage (the GUI's value), then severity / breadth.
    ordered = sorted(
        stats,
        key=lambda s: (s.exposure, severity_rank(s.severity), s.principals),
        reverse=True,
    )
    breakdown = [
        {
            "finding": s.finding,
            "severity": s.severity,
            "principals": s.principals,
            "exposure": _as_percent(s.exposure),
            "impact": _as_percent(s.impact),
        }
        for s in ordered
    ]

    if settings(ctx).as_json:
        print_json(
            {
                "domain": name,
                "exposure": _as_percent(headline.get("exposure_index")) if headline else None,
                "tier_zero": headline.get("tier_zero_count") if headline else None,
                "critical": headline.get("critical_risk_count") if headline else None,
                "findings": breakdown,
            }
        )
        return

    if headline:
        console.print(
            f"[bold]{name}[/bold] - exposure [bold]{_as_percent(headline.get('exposure_index'))}[/bold]: "
            "that share of this domain's principals can reach a Tier Zero asset through some attack "
            f"path ([bold]{headline.get('tier_zero_count', 0)}[/bold] Tier Zero assets, "
            f"[bold]{headline.get('critical_risk_count', 0)}[/bold] critical findings)."
        )
    console.print(
        "[dim]In plain terms: 'exposure' is the % of this domain's users/computers/groups that can "
        "take over a most-privileged (Tier Zero) asset. The findings below are what create that "
        "exposure - fix high-severity, high-exposure rows first; they cut the most paths. "
        "(Per-finding exposures overlap heavily, so they don't add up to the domain total.)\n"
        "Columns -> exposure: % of principals this finding exposes to Tier Zero (BHE's "
        "ExposurePercentage); impact: share of the attack-path surface it accounts for "
        "(ImpactPercentage); principals: how many are affected.[/dim]"
    )
    if breakdown:
        output(
            ctx, breakdown,
            columns=["finding", "severity", "principals", "exposure", "impact"],
            title=f"Exposure breakdown - {name}",
        )
    else:
        console.print("[green]No attack-path findings drive this domain's exposure.[/green]")
    if failures:
        console.print(f"[dim]{failures} finding query(ies) failed and were skipped.[/dim]")


@app.command()
def posture(
    ctx: typer.Context,
    domain: str = typer.Argument(
        None, help="Optional domain name/id to scope to (e.g. ESSOS.LOCAL)."
    ),
    explain: bool = typer.Option(
        False, "--explain", "-e",
        help="Break down what drives one domain's exposure (requires a domain).",
    ),
    history: bool = typer.Option(
        False, "--history", help="Show every snapshot, not just the latest per domain."
    ),
) -> None:
    """Risk-posture stats per domain (latest snapshot, ranked by exposure).

    Pass a domain to scope to just that one; add ``--explain`` to see the findings
    that drive that domain's exposure percentage.
    """
    if explain:
        if not domain:
            err_console.print(
                "[red]Give a domain to explain, e.g. `bhe posture ESSOS.LOCAL --explain`.[/red]"
            )
            raise typer.Exit(code=2)
        _posture_explain(ctx, domain)
        return

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
    # Render exposure as a percentage (the index is 0-1; the web GUI shows /100).
    for row in out:
        row["exposure"] = _as_percent(row["exposure"])
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
            exposure=float(sample.get("ExposurePercentage") or 0.0),
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


@app.command("tier-zero")
def tier_zero_cmd(
    ctx: typer.Context,
    domain: str = typer.Argument(None, help="Optional domain name/id to scope to."),
) -> None:
    """List Tier Zero / high-value principals (optionally one domain).

    These are the crown jewels everything else is measured against - the seed set
    ``choke`` / ``leaks`` / ``map`` funnel into.
    """
    from bhe.parsing.graph import nodes_table

    async def _go(c):
        dom = await resolve_domain(c, domain) if domain else None
        return nodes_table(await c.cypher_query(tier_zero_seed_query())), dom

    rows, dom = run(ctx, _go)
    if dom is not None:
        want = str(dom.get("name", "")).upper()
        rows = [r for r in rows if str(r.get("domain", "")).upper() == want]
    rows.sort(key=lambda r: (str(r.get("domain", "")), str(r.get("kind", "")), str(r.get("name", ""))))
    if settings(ctx).as_json:
        print_json(rows)
        return
    scope = f" - {dom.get('name')}" if dom is not None else ""
    output(ctx, rows, columns=["name", "kind", "domain", "objectid"], title=f"Tier Zero{scope}")


def _latest_quality_row(rows: list) -> dict:
    """The most recent data-quality snapshot (data-quality-stats is historical)."""
    return max(rows, key=lambda r: str(r.get("created_at", "")), default={})


@app.command()
def quality(
    ctx: typer.Context,
    domain: str = typer.Argument(None, help="Optional AD domain to detail (e.g. CORP.LOCAL)."),
) -> None:
    """Collection data-quality / completeness - is the data trustworthy?

    No domain: estate completeness (% of local admins & sessions collected). With a
    domain: that domain's latest collection counts + completeness. Run this BEFORE
    trusting findings - missing sessions/local-groups blind the attack-path analysis.
    """
    from bhe.api.client import BHEClientError

    async def _go(c):
        dom = await resolve_domain(c, domain) if domain else None
        if dom is not None:
            try:
                resp = await c.get_ad_domain_quality(dom["id"])
            except BHEClientError:
                resp = None
            return "domain", dom, _latest_quality_row(_posture_rows(resp) if resp else [])
        comp = await c.get_completeness()
        return "estate", None, (comp.get("data", comp) if isinstance(comp, dict) else comp)

    mode, dom, payload = run(ctx, _go)
    if settings(ctx).as_json:
        print_json(payload)
        return
    if mode == "estate":
        # The completeness map is all 0-1 ratios -> show as percentages.
        data = {
            k: (_as_percent(v) if isinstance(v, (int, float)) else v)
            for k, v in (payload or {}).items()
        }
        if data:
            output(ctx, data, title="Collection completeness")
        else:
            console.print("[dim]No completeness data returned.[/dim]")
        return
    name = dom.get("name") or domain
    if payload:
        # Counts stay numeric; the *_completeness ratios render as percentages.
        row = {
            k: (_as_percent(v) if "completeness" in k.lower() and isinstance(v, (int, float)) else v)
            for k, v in payload.items()
        }
        output(ctx, row, title=f"Data quality - {name}")
    else:
        console.print(f"[yellow]No data-quality stats for {name}.[/yellow]")


# ----------------------------------------------------------------------------
# Collection: clients, jobs, schedules
# ----------------------------------------------------------------------------


@app.command()
def clients(ctx: typer.Context) -> None:
    """List registered collection clients (GET /api/v2/clients)."""
    output(ctx, run(ctx, lambda c: c.get_clients()), title="Clients")


@app.command()
def client(
    ctx: typer.Context,
    client_id: str = typer.Argument(..., help="Client id, an id fragment, or name."),
) -> None:
    """Show a single collection client's detail.

    Accepts the full id, a partial/last-segment id (e.g. ``41bf0d42c999``), or the
    client name - so you don't have to copy the whole GUID out of the terminal.
    """
    from bhe.cli._resolve import resolve_client

    async def _go(c):
        cl = await resolve_client(c, client_id)
        return await c.get_client(cl["id"])

    output(ctx, run(ctx, _go), title=f"Client {client_id}")


@app.command()
def jobs(
    ctx: typer.Context,
    current: bool = typer.Option(False, "--current", help="Only running jobs."),
    finished: bool = typer.Option(False, "--finished", help="Only finished jobs."),
) -> None:
    """List collection jobs, correlated to their client (name/host) so you never
    have to deal with raw client GUIDs."""

    async def _go(c):
        return await c.get_jobs(), await c.get_clients()

    raw_jobs, clients = run(ctx, _go)
    # Filter client-side by end-time: BHE has no /jobs/current or /finished route.
    if current:
        raw_jobs = [j for j in raw_jobs if not j.get("end_time")]
    elif finished:
        raw_jobs = [j for j in raw_jobs if j.get("end_time")]
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
# Audit log
# ----------------------------------------------------------------------------


def _audit_entries(raw) -> list:
    """Unwrap the audit envelope into a flat list (``data`` / ``data.logs`` / list)."""
    data = raw.get("data", raw) if isinstance(raw, dict) else raw
    if isinstance(data, dict):
        for key in ("logs", "audit_logs", "entries"):
            if isinstance(data.get(key), list):
                return data[key]
        return []
    return data if isinstance(data, list) else []


def _ci_get(entry, *keys: str):
    """First non-empty value among case-insensitive key variants (version-tolerant)."""
    if not isinstance(entry, dict):
        return ""
    low = {k.lower(): v for k, v in entry.items()}
    for key in keys:
        val = low.get(key.lower())
        if val not in (None, ""):
            return val
    return ""


def _strip_domain(name) -> str:
    """Username without its domain qualifier: ``CORP\\u`` / ``u@corp.local`` -> ``u``."""
    s = str(name or "")
    if "\\" in s:
        s = s.rsplit("\\", 1)[1]
    if "@" in s:
        s = s.split("@", 1)[0]
    return s


def _audit_actor(entry) -> str:
    """Best actor identifier for an audit entry (a username/email, then a name)."""
    return str(_ci_get(entry, "actor_email", "actor_principal", "actor_name", "actor", "actor_id"))


# Substrings that mark a login action.  Deliberately narrow ("auth" would also
# match CreateAuthToken); for any tenant-specific naming, use --action <name>.
_LOGIN_TOKENS = ("login", "logon", "signin")


def _is_login(action) -> bool:
    """True if an action looks like an authentication/login event."""
    a = str(action).lower()
    return any(tok in a for tok in _LOGIN_TOKENS)


@app.command()
def audit(
    ctx: typer.Context,
    user: str = typer.Option(
        None, "--user", "-u", help="Filter to one actor (name/email substring)."
    ),
    action: str = typer.Option(
        None, "--action", "-a", help="Filter to an action substring (e.g. login)."
    ),
    logins: bool = typer.Option(False, "--logins", help="Only authentication/login events."),
    days: int = typer.Option(7, "--days", help="Look back this many days."),
    since: str = typer.Option(None, "--since", help="RFC3339/ISO start time (overrides --days)."),
    limit: int = typer.Option(200, "--limit", "-n", help="Max entries to fetch."),
    last_per_user: bool = typer.Option(
        False, "--last-per-user", help="Most recent event per actor (a 'last login' view)."
    ),
) -> None:
    """Platform audit log: who logged in / acted, and when (shown in local time).

    Filtering is done client-side so it's robust across BHE versions; usernames
    are shown without their domain. Use ``--logins`` for authentication events,
    ``--user`` to scope to one actor, or ``--last-per-user`` for a 'who logged in
    last' summary.
    """
    from datetime import datetime, timedelta, timezone

    cutoff = since or (
        (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    )
    params = {"after": cutoff, "limit": limit, "sort_by": "-created_at"}
    raw = run(ctx, lambda c: c.get_audit_log(params=params))

    entries = _audit_entries(raw)
    # Belt-and-suspenders client-side window + newest-first sort (ISO-UTC strings
    # sort lexicographically), so we don't depend on the server honouring after/sort.
    entries = [e for e in entries if str(_ci_get(e, "created_at", "timestamp")) >= cutoff]
    entries.sort(key=lambda e: str(_ci_get(e, "created_at", "timestamp")), reverse=True)

    if logins:
        entries = [e for e in entries if _is_login(_ci_get(e, "action"))]
    if action:
        entries = [e for e in entries if action.lower() in str(_ci_get(e, "action")).lower()]
    if user:
        needle = user.lower()
        entries = [
            e for e in entries
            if needle in " ".join(
                str(_ci_get(e, k)) for k in
                ("actor_email", "actor_name", "actor", "actor_id", "actor_principal")
            ).lower()
        ]
    if last_per_user:
        seen: dict = {}
        for e in entries:  # already newest-first
            seen.setdefault(_audit_actor(e), e)
        entries = list(seen.values())

    rows = [
        {
            "time": _to_local(str(_ci_get(e, "created_at", "timestamp"))),
            "user": _strip_domain(_audit_actor(e)),
            "action": _ci_get(e, "action"),
            "status": _ci_get(e, "status", "result"),
            "source": _ci_get(e, "source_ip_address", "source", "source_addr", "remote_addr"),
        }
        for e in entries[:limit]
    ]
    scope = " - logins" if logins else ""
    if user:
        scope += f" - {user}"
    output(ctx, rows, columns=["time", "user", "action", "status", "source"], title=f"Audit log{scope}")


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

_ENTITY_PLURAL = {
    "user": "users", "computer": "computers", "group": "groups",
    "domain": "domains", "gpo": "gpos",
}

# --show aspect -> (endpoint slug, the kinds it applies to).
_ENTITY_RELS = {
    "sessions": ("sessions", {"user", "computer", "group"}),
    "members": ("members", {"group"}),
    "memberships": ("memberships", {"user", "group"}),
    "admin-rights": ("admin-rights", {"user", "computer", "group"}),
    "admins": ("admin-users", {"computer"}),
    "controllers": ("controllers", {"user", "computer", "group", "domain", "gpo"}),
    "controllables": ("controllables", {"user", "computer", "group"}),
}


@app.command()
def entity(
    ctx: typer.Context,
    selector: str = typer.Argument(..., help="Principal name or objectid."),
    kind: str = typer.Option(
        None, "--kind", "-k", help=f"Hint the kind: {', '.join(_ENTITY_GETTERS)}."
    ),
    show: str = typer.Option(
        None, "--show", "-s",
        help=f"Pivot to a relationship instead of properties: {', '.join(_ENTITY_RELS)}.",
    ),
) -> None:
    """Show entity detail by NAME or objectid; ``--show`` pivots to a relationship.

    Default view is the node's properties. ``--show sessions`` (who logged in
    where), ``--show members`` / ``--show admin-rights`` / ``--show controllers``
    (the ACL attack surface), etc. surface the node's relationships.
    """
    aspect = show.lower() if show else None
    if aspect and aspect not in _ENTITY_RELS:
        err_console.print(
            f"[red]Unknown --show '{show}'.[/red] Choose one of: {', '.join(_ENTITY_RELS)}."
        )
        raise typer.Exit(code=2)

    async def _go(c):
        node = await resolve_principal(c, selector, kind)
        node_kind = (kind or node.get("type") or "").lower()
        oid = node.get("objectid")
        name = node.get("name", selector)
        if aspect:
            slug, kinds = _ENTITY_RELS[aspect]
            plural = _ENTITY_PLURAL.get(node_kind)
            if not oid or not plural or node_kind not in kinds:
                return {"__rel_error__":
                        f"'{aspect}' isn't available for {name} ({node_kind or 'unknown kind'})."}
            from bhe.parsing.graph import parse_graph

            # Prefer the graph form (its edges carry the permission/right). If the
            # tenant returns no edges for it, fall back to the flat list so we still
            # show the related objects (the view that worked before).
            graph = await c.get_entity_relationship(plural, oid, slug, params={"type": "graph"})
            if parse_graph(graph)[1]:  # has edges
                return {"__rel_graph__": graph, "__name__": name}
            listed = await c.get_entity_relationship(plural, oid, slug)
            return {"__rel_list__": listed, "__name__": name}
        method = _ENTITY_GETTERS.get(node_kind)
        if method and oid:
            return await getattr(c, method)(oid)
        return node  # fall back to the resolved search record

    result = run(ctx, _go)

    if isinstance(result, dict) and "__rel_error__" in result:
        err_console.print(f"[yellow]{result['__rel_error__']}[/yellow]")
        raise typer.Exit(code=1)
    if isinstance(result, dict) and "__rel_graph__" in result:
        from bhe.parsing.graph import parse_graph

        graph, name = result["__rel_graph__"], result["__name__"]
        if settings(ctx).as_json:
            print_json(graph)
            return
        nodes, edges = parse_graph(graph)
        by_link = {n.link_id: n for n in nodes if n.link_id}

        def _nm(link: str) -> str:
            n = by_link.get(link)
            return ((n.properties or {}).get("name") or n.label or n.object_id) if n else link

        erows = [
            {"from": _nm(e.source), "right": e.kind or e.label or "", "to": _nm(e.target)}
            for e in edges
        ]
        output(ctx, erows, columns=["from", "right", "to"], title=f"{name} - {aspect}")
        return
    if isinstance(result, dict) and "__rel_list__" in result:
        from bhe.parsing.graph import nodes_table

        listed, name = result["__rel_list__"], result["__name__"]
        if settings(ctx).as_json:
            print_json(listed)
            return
        rows = nodes_table(listed)
        if not rows:  # not a graph payload - fall back to a list/count envelope
            data = listed.get("data", listed) if isinstance(listed, dict) else listed
            rows = data if isinstance(data, list) else []
        if rows:
            output(ctx, rows, title=f"{name} - {aspect}")
        else:
            console.print(f"[dim]No {aspect} for {name}.[/dim]")
        return

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
    targets = choke_targets(snapshot, [cp.objectid for cp in chokes])
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
                        "reaches_t0": targets.get(cp.objectid, ("?", 0))[0],
                        "tier_zero_targets": targets.get(cp.objectid, ("?", 0))[1],
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
    def _reaches(oid: str) -> str:
        name, n = targets.get(oid, ("?", 0))
        return f"{name} (+{n - 1})" if n > 1 else name

    rows = [
        {
            "rank": i + 1,
            "choke_point": cp.name,
            "kind": cp.kind,
            "depth": cp.depth,
            "principals_cut": cp.principals_cut,
            "pct": f"{cp.pct_cut * 100:.0f}%",
            "reaches_t0": _reaches(cp.objectid),
        }
        for i, cp in enumerate(chokes)
    ]
    output(
        ctx,
        rows,
        columns=["rank", "choke_point", "kind", "depth", "principals_cut", "pct", "reaches_t0"],
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

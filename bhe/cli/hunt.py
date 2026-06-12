"""``bhe hunt`` - guided attack-path recipes that write the Cypher for you.

Each recipe turns friendly inputs (principal names, a domain) into a read-only
Cypher query, echoes the generated query to stderr for transparency (so it
doubles as a way to *learn* Cypher), runs it, and renders the resulting nodes.
``--dry-run`` prints the query to stdout without running it.

Adding a recipe is a few lines here plus a builder in ``bhe.queries.library``.
"""

from __future__ import annotations

import typer

from bhe.cli._resolve import resolve_principal
from bhe.cli._runtime import (
    console,
    err_console,
    output,
    print_json,
    run,
    settings,
)
from bhe.parsing.graph import extract_literals, nodes_table
from bhe.queries import library

hunt_app = typer.Typer(no_args_is_help=True, help="Guided attack-path recipes (auto-generate Cypher).")


def _render(ctx: typer.Context, result, title: str, empty: str) -> None:
    """Render a Cypher result as nodes / literals, or an empty-result note."""
    if settings(ctx).as_json:
        print_json(result)
        return
    rows = nodes_table(result)
    if rows:
        output(ctx, rows, title=title)
        return
    literals = extract_literals(result)
    if literals:
        output(ctx, [{"value": v} for v in literals], title=title)
    else:
        console.print(f"[dim]{empty}[/dim]")


def _render_paths(ctx: typer.Context, result, title: str, empty: str, max_paths: int = 25) -> None:
    """Render path results as escalation chains (the ordered source->target edges).

    A node table loses the relationships AND their order; for an attack path the
    edges *are* the answer, so we show one ``step/from/edge/to`` table per path
    (shortest first).  Falls back to the node list if no path can be reconstructed.
    """
    from bhe.parsing.graph import path_hops

    if settings(ctx).as_json:
        print_json(result)
        return
    paths = path_hops(result)
    if not paths:
        rows = nodes_table(result)
        if rows:  # nodes came back but no traversable chain - show what we have
            output(ctx, rows, title=title)
        else:
            console.print(f"[dim]{empty}[/dim]")
        return
    for i, hops in enumerate(paths[:max_paths]):
        suffix = (
            f"path {i + 1}/{len(paths)}, {len(hops)} hop(s)"
            if len(paths) > 1
            else f"{len(hops)} hop(s)"
        )
        output(ctx, hops, columns=["step", "from", "edge", "to"], title=f"{title}  ({suffix})")
    if len(paths) > max_paths:
        console.print(
            f"[dim]... and {len(paths) - max_paths} more path(s); "
            "narrow with a specific target or drop --all.[/dim]"
        )


def _recipe(ctx: typer.Context, query: str, title: str, dry_run: bool) -> None:
    """Echo + run a domain-scoped query (no principal to resolve)."""
    if dry_run:
        print(query)  # raw stdout (no Rich wrapping) so it pastes cleanly
        return
    err_console.print(f"[dim]cypher> {query}[/dim]")  # transparency, off stdout
    result = run(ctx, lambda c: c.cypher_query(query))
    _render(ctx, result, title, "(no results)")


def _principal_recipe(ctx, *, selectors, build, dry_run, target_desc=None) -> None:
    """Resolve principal selector(s) -> objectids, then build + run the Cypher.

    Resolution goes through :func:`resolve_principal`, so a selector can be a
    partial name, an exact name, or a raw objectid, and an ambiguous name raises
    a ``ResolutionError`` (candidates + exit 2) rather than silently guessing.
    It runs for ``--dry-run`` too, so the printed query is the real one that would
    execute - matched by the resolved objectid, with the names shown for context.
    """
    async def _go(c):
        resolved = [await resolve_principal(c, sel) for sel in selectors]
        oids = [r.get("objectid") or sel for r, sel in zip(resolved, selectors)]
        names = [r.get("name") or sel for r, sel in zip(resolved, selectors)]
        query = build(*oids)
        result = None if dry_run else await c.cypher_query(query)
        return query, names, result

    query, names, result = run(ctx, _go)
    label = " -> ".join(names)
    title = f"{label} -> {target_desc}" if target_desc else label
    if dry_run:
        # Raw stdout (no Rich wrapping) so the query pastes cleanly; the leading
        # `//` line is a valid Cypher comment recording who resolved to what.
        print(f"// {title}")
        print(query)
        return
    err_console.print(f"[dim]{title}[/dim]")
    err_console.print(f"[dim]cypher> {query}[/dim]")
    _render_paths(ctx, result, title, f"(no results - no path from {names[0]} to the target set)")


@hunt_app.command()
def path(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="Source principal name."),
    target: str = typer.Argument(..., help="Target principal name."),
    all_paths: bool = typer.Option(False, "--all", "-a", help="All shortest paths."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the Cypher, don't run."),
) -> None:
    """Shortest attack path between two named principals."""
    build = library.all_shortest_paths if all_paths else library.shortest_path
    _principal_recipe(ctx, selectors=[source, target], build=build, dry_run=dry_run)


@hunt_app.command("tier-zero")
def tier_zero(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="Source principal name."),
    all_paths: bool = typer.Option(False, "--all", "-a", help="All shortest paths."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the Cypher, don't run."),
) -> None:
    """Paths from a principal to ANY Tier Zero / high-value target."""
    _principal_recipe(
        ctx,
        selectors=[source],
        build=lambda s: library.path_to_tier_zero(s, all_paths=all_paths),
        dry_run=dry_run,
        target_desc="Tier Zero",
    )


@hunt_app.command()
def hybrid(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="On-prem AD principal name."),
    all_paths: bool = typer.Option(False, "--all", "-a", help="All shortest paths."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the Cypher, don't run."),
) -> None:
    """Hybrid path from an on-prem AD principal to ANY Azure/Entra node."""
    _principal_recipe(
        ctx,
        selectors=[source],
        build=lambda s: library.hybrid_path_to_azure(s, all_paths=all_paths),
        dry_run=dry_run,
        target_desc="Azure/Entra",
    )


@hunt_app.command()
def kerberoastable(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Domain name, e.g. CORP.LOCAL."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the Cypher, don't run."),
) -> None:
    """Enabled users with an SPN (Kerberoastable) in a domain."""
    _recipe(ctx, library.kerberoastable_users(domain), f"Kerberoastable - {domain}", dry_run)


@hunt_app.command()
def asrep(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Domain name, e.g. CORP.LOCAL."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the Cypher, don't run."),
) -> None:
    """Enabled users with DontReqPreauth (AS-REP roastable) in a domain."""
    _recipe(ctx, library.asrep_roastable_users(domain), f"AS-REP roastable - {domain}", dry_run)

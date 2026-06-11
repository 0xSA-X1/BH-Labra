"""``bhe hunt`` - guided attack-path recipes that write the Cypher for you.

Each recipe turns friendly inputs (principal names, a domain) into a read-only
Cypher query, echoes the generated query to stderr for transparency (so it
doubles as a way to *learn* Cypher), runs it, and renders the resulting nodes.
``--dry-run`` prints the query to stdout without running it.

Adding a recipe is a few lines here plus a builder in ``bhe.queries.library``.
"""

from __future__ import annotations

import typer

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


def _recipe(ctx: typer.Context, query: str, title: str, dry_run: bool) -> None:
    """Echo + run a generated query, rendering nodes (or print it for --dry-run)."""
    if dry_run:
        console.print(query)  # stdout: the query is the deliverable
        return
    err_console.print(f"[dim]cypher> {query}[/dim]")  # transparency, off stdout
    result = run(ctx, lambda c: c.cypher_query(query))
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
        console.print("[dim](no results)[/dim]")


@hunt_app.command()
def path(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="Source principal name."),
    target: str = typer.Argument(..., help="Target principal name."),
    all_paths: bool = typer.Option(False, "--all", "-a", help="All shortest paths."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the Cypher, don't run."),
) -> None:
    """Shortest attack path between two named principals."""
    query = (
        library.all_shortest_paths(source, target)
        if all_paths
        else library.shortest_path(source, target)
    )
    _recipe(ctx, query, f"{source} -> {target}", dry_run)


@hunt_app.command("tier-zero")
def tier_zero(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="Source principal name."),
    all_paths: bool = typer.Option(False, "--all", "-a", help="All shortest paths."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the Cypher, don't run."),
) -> None:
    """Paths from a principal to ANY Tier Zero / high-value target."""
    query = library.path_to_tier_zero(source, all_paths=all_paths)
    _recipe(ctx, query, f"{source} -> Tier Zero", dry_run)


@hunt_app.command()
def hybrid(
    ctx: typer.Context,
    source: str = typer.Argument(..., help="On-prem AD principal name."),
    all_paths: bool = typer.Option(False, "--all", "-a", help="All shortest paths."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the Cypher, don't run."),
) -> None:
    """Hybrid path from an on-prem AD principal to ANY Azure/Entra node."""
    query = library.hybrid_path_to_azure(source, all_paths=all_paths)
    _recipe(ctx, query, f"{source} -> Azure/Entra", dry_run)


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

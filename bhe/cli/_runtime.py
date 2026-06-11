"""Shared CLI plumbing: settings, the async-client runner, and output rendering.

Every command is a thin sync Typer function that calls :func:`run` with a small
async lambda over the connected :class:`~bhe.api.client.BHEClient`, then hands the
result to :func:`output`.  Connection / auth / read-only errors are rendered
cleanly here and mapped to non-zero exit codes, so commands stay one-liners.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import typer
from rich.console import Console
from rich.table import Table

from bhe.api.client import BHEClient, BHEClientError
from bhe.api.readonly import ReadOnlyViolation
from bhe.cli._resolve import ResolutionError
from bhe.config import TenantProfile, load_profile
from bhe.observability import RequestLog, log_file_for

console = Console()
err_console = Console(stderr=True)


@dataclass
class Settings:
    """Global flags parsed by the root callback, stashed on ``ctx.obj``."""

    profile: str | None = None
    mock: bool = False
    profiles_file: str | None = None
    as_json: bool = False


def settings(ctx: typer.Context) -> Settings:
    """The global settings for this invocation."""
    return ctx.obj  # set by the root callback in app.py


def resolve_profile(s: Settings) -> TenantProfile:
    """Resolve the connection profile, or exit 2 with a friendly message."""
    try:
        profile = load_profile(
            s.profile, mock=s.mock or None, profiles_file=s.profiles_file
        )
    except (KeyError, FileNotFoundError) as exc:
        err_console.print(f"[red]Config error:[/red] {exc}")
        raise typer.Exit(code=2)

    if not profile.is_complete():
        err_console.print(
            "[red]Profile is incomplete.[/red] Set BHE_TENANT_URL and BHE_TOKEN_ID, "
            "store a Token Key ([bold]bhe keyring set <profile>[/bold]), or use --mock."
        )
        raise typer.Exit(code=2)
    return profile


def run(ctx: typer.Context, fn: Callable[[BHEClient], Awaitable[Any]]) -> Any:
    """Open a client for the resolved profile, run ``fn(client)``, return its result.

    The read-only guard and request logging come along for free via the shared
    client.  Live profiles also mirror the request trace to the rotating per-tenant
    log so ``bhe bundle`` has something to ship.
    """
    s = settings(ctx)
    profile = resolve_profile(s)
    request_log = RequestLog()
    if not profile.mock:
        request_log.attach_file(log_file_for(profile.name))

    async def _go() -> Any:
        async with BHEClient.connect(
            profile.base_url,
            profile.token_id,
            profile.token_key or "",
            mock=profile.mock,
            request_log=request_log,
        ) as client:
            return await fn(client)

    try:
        return asyncio.run(_go())
    except ResolutionError as exc:
        err_console.print(f"[yellow]{exc}[/yellow]")
        if exc.candidates:
            _print_table(exc.candidates, exc.columns, "Candidates")
        if exc.hint:
            err_console.print(f"[dim]{exc.hint}[/dim]")
        raise typer.Exit(code=2)
    except ReadOnlyViolation as exc:
        err_console.print(f"[magenta]Blocked (read-only):[/magenta] {exc}")
        raise typer.Exit(code=1)
    except BHEClientError as exc:
        err_console.print(f"[red]API error {exc.status_code}:[/red] {exc.detail}")
        raise typer.Exit(code=1)
    except Exception as exc:  # noqa: BLE001 - one clean error line, never a traceback
        err_console.print(f"[red]{type(exc).__name__}:[/red] {exc}")
        raise typer.Exit(code=1)


# ----------------------------------------------------------------------------
# Output rendering
# ----------------------------------------------------------------------------


def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str)
    return str(value)


def _unwrap(result: Any) -> Any:
    """Unwrap BHE's ``{"data": ...}`` envelope for friendlier human display."""
    if isinstance(result, dict) and "data" in result:
        return result["data"]
    return result


def print_json(result: Any) -> None:
    console.print_json(json.dumps(result, default=str))


def _print_table(rows: list, columns: list[str] | None, title: str | None) -> None:
    if not rows:
        console.print("[dim](no rows)[/dim]")
        return
    if not isinstance(rows[0], dict):
        for row in rows:  # list of scalars
            console.print(_fmt(row))
        return
    if columns is None:
        # Infer up to 8 scalar columns from the first row.
        columns = [k for k, v in rows[0].items() if not isinstance(v, (dict, list))][:8]
    table = Table(title=title, header_style="bold cyan")
    for col in columns:
        table.add_column(col, overflow="fold")
    for row in rows:
        table.add_row(*[_fmt(row.get(col)) for col in columns])
    console.print(table)
    console.print(f"[dim]{len(rows)} row(s)[/dim]")


def _print_kv(obj: dict, title: str | None) -> None:
    table = Table(title=title, header_style="bold cyan", show_header=False)
    table.add_column("field", style="bold")
    table.add_column("value", overflow="fold")
    for key, value in obj.items():
        table.add_row(key, _fmt(value))
    console.print(table)


def output(
    ctx: typer.Context,
    result: Any,
    *,
    columns: list[str] | None = None,
    title: str | None = None,
) -> None:
    """Render ``result`` as raw JSON (``--json``) or a human table / key-value view."""
    if settings(ctx).as_json:
        print_json(result)
        return
    payload = _unwrap(result)
    if isinstance(payload, list):
        _print_table(payload, columns, title)
    elif isinstance(payload, dict):
        _print_kv(payload, title)
    else:
        console.print(_fmt(payload))

"""Name -> object resolution so users never have to paste opaque IDs.

Every selector a command accepts can be a human name *or* a raw id.  These
helpers turn a selector into the concrete record, and - crucially - when a name
is ambiguous they raise :class:`ResolutionError` carrying the candidate list so
the CLI can show "did you mean..." rather than silently guessing.  Nothing here
prompts interactively, so commands stay pipe-/script-friendly.
"""

from __future__ import annotations

import re
from typing import Any

from bhe.api.client import BHEClient

# A selector that looks like a SID or a GUID is treated as an id, not a name.
_SID_RE = re.compile(r"^S-1-\d", re.IGNORECASE)
_GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


class ResolutionError(Exception):
    """A selector matched zero or many records; carries candidates for display."""

    def __init__(
        self,
        message: str,
        *,
        candidates: list[dict[str, Any]] | None = None,
        columns: list[str] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(message)
        self.candidates = candidates or []
        self.columns = columns
        self.hint = hint


def looks_like_id(selector: str) -> bool:
    """True if the selector is a SID/GUID (an id) rather than a friendly name."""
    s = selector.strip()
    return bool(_SID_RE.match(s) or _GUID_RE.match(s))


async def resolve_domain(client: BHEClient, selector: str) -> dict[str, Any]:
    """Resolve a domain by id, exact name, or unique name substring.

    Raises:
        ResolutionError: on no match (lists all domains) or an ambiguous
            substring (lists the matches).
    """
    domains = await client.get_available_domains()
    low = selector.strip().lower()

    for dom in domains:
        if str(dom.get("id", "")).lower() == low or str(dom.get("name", "")).lower() == low:
            return dom

    matches = [d for d in domains if low in str(d.get("name", "")).lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ResolutionError(
            f"No domain matches '{selector}'.",
            candidates=domains,
            columns=["name", "id", "type", "collected"],
            hint="Run `bhe domains` to list them.",
        )
    raise ResolutionError(
        f"'{selector}' is ambiguous - {len(matches)} domains match.",
        candidates=matches,
        columns=["name", "id", "type", "collected"],
        hint="Re-run with a more specific name or the id.",
    )


async def resolve_client(client: BHEClient, selector: str) -> dict[str, Any]:
    """Resolve a collection client by full id, a partial/last-segment id, or name.

    Client ids are long GUIDs that are awkward to copy whole from a terminal, so a
    fragment (e.g. the last segment ``41bf0d42c999``) or the client name resolves
    too - uniquely, or it raises with the candidates.
    """
    clients = await client.get_clients()
    low = selector.strip().lower()

    for cl in clients:
        if str(cl.get("id", "")).lower() == low:
            return cl

    matches = [
        cl for cl in clients
        if low in str(cl.get("id", "")).lower() or low in str(cl.get("name", "")).lower()
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ResolutionError(
            f"No client matches '{selector}'.",
            candidates=clients,
            columns=["name", "id", "hostname", "type"],
            hint="Run `bhe clients` to list them.",
        )
    raise ResolutionError(
        f"'{selector}' is ambiguous - {len(matches)} clients match.",
        candidates=matches,
        columns=["name", "id", "hostname", "type"],
        hint="Use a longer id fragment.",
    )


async def resolve_principal(
    client: BHEClient, selector: str, kind: str | None = None
) -> dict[str, Any]:
    """Resolve a principal (user/computer/group/...) by name, or accept a raw id.

    Returns a record with at least ``objectid``, ``name`` and ``type`` keys.

    Raises:
        ResolutionError: on no match or an ambiguous name (lists candidates).
    """
    if looks_like_id(selector):
        return {"objectid": selector, "name": selector, "type": kind}

    hits = await client.search(selector, kind)
    if kind:
        hits = [h for h in hits if str(h.get("type", "")).lower() == kind.lower()]

    # Prefer an exact name match before falling back to all substring hits.
    exact = [h for h in hits if str(h.get("name", "")).lower() == selector.lower()]
    pool = exact or hits

    if len(pool) == 1:
        return pool[0]
    if not pool:
        raise ResolutionError(
            f"No principal matches '{selector}'.",
            hint="Try `bhe search <term>` to explore.",
        )
    raise ResolutionError(
        f"'{selector}' is ambiguous - {len(pool)} matches.",
        candidates=pool,
        columns=["name", "type", "objectid"],
        hint="Re-run with the exact name, add --kind, or use the objectid.",
    )

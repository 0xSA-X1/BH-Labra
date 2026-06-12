"""A small synthetic attack-path *funnel* the mock serves for backward scoping.

Real backward BFS needs a graph that answers different one-hop questions for
different node sets — something the static cypher fixture can't do.  This module
defines a tiny but realistic funnel into Tier Zero and renders BHE-shaped graph
responses for (a) the Tier Zero seed query and (b) inbound-expansion queries, so
``bhe --mock choke --tier-zero`` runs the whole engine end to end:

    AUTHENTICATED USERS ─┐
    ALICE ───────────────┼─MemberOf→ HELPDESK ─GenericAll→ DOMAIN ADMINS (T0)
    BOB ─────────────────┘                                   ▲   ▲
    BACKUP-SVC ─────────────────────────────DCSync───────────┘   │
    DEV-ADMIN (DEV domain) ─────────────────GenericAll──────────-┘  (cross-domain leak)

HELPDESK is the dominant choke point (cuts AUTH USERS + ALICE + BOB); BACKUP-SVC
and DEV-ADMIN are independent direct paths that survive removing it.
"""

from __future__ import annotations

import re
from typing import Any

_C = "S-1-5-21-1111111111-2222222222-3333333333"   # CORP domain prefix
_D = "S-1-5-21-4444444444-5555555555-6666666666"   # DEV domain prefix

# objectid -> (name, kind, tier_zero)
NODES: dict[str, tuple[str, str, bool]] = {
    f"{_C}-512": ("DOMAIN ADMINS@CORP.LOCAL", "Group", True),
    f"{_C}-1140": ("HELPDESK@CORP.LOCAL", "Group", False),
    f"{_C}-AU": ("AUTHENTICATED USERS@CORP.LOCAL", "Group", False),
    f"{_C}-1105": ("ALICE@CORP.LOCAL", "User", False),
    f"{_C}-1106": ("BOB@CORP.LOCAL", "User", False),
    f"{_C}-1109": ("BACKUP-SVC@CORP.LOCAL", "User", False),
    f"{_D}-1200": ("DEV-ADMIN@DEV.CORP.LOCAL", "User", False),
}

# (source_objectid, edge_type, target_objectid) — directed toward Tier Zero.
EDGES: list[tuple[str, str, str]] = [
    (f"{_C}-1140", "GenericAll", f"{_C}-512"),   # HELPDESK -> DA
    (f"{_C}-AU", "MemberOf", f"{_C}-1140"),       # AUTH USERS -> HELPDESK
    (f"{_C}-1105", "MemberOf", f"{_C}-1140"),     # ALICE -> HELPDESK
    (f"{_C}-1106", "MemberOf", f"{_C}-1140"),     # BOB -> HELPDESK
    (f"{_C}-1109", "DCSync", f"{_C}-512"),         # BACKUP-SVC -> DA (direct)
    (f"{_D}-1200", "GenericAll", f"{_C}-512"),     # DEV-ADMIN -> DA (cross-domain leak)
]


def _node_obj(oid: str, graph_id: str) -> dict[str, Any]:
    name, kind, _tz = NODES[oid]
    domain = "DEV.CORP.LOCAL" if "@DEV" in name else "CORP.LOCAL"
    return {
        "objectId": oid,
        "label": name,
        "kind": kind,
        "properties": {"name": name, "objectid": oid, "domain": domain},
    }


def _graph(node_oids: list[str], edges: list[tuple[str, str, str]]) -> dict[str, Any]:
    """Render a BHE ``{"data": {"nodes": {...}, "edges": [...]}}`` payload."""
    ordered = list(dict.fromkeys(node_oids))
    gid = {oid: str(i + 1) for i, oid in enumerate(ordered)}
    nodes = {gid[oid]: _node_obj(oid, gid[oid]) for oid in ordered}
    rendered_edges = [
        {"source": gid[s], "target": gid[t], "label": et, "kind": et}
        for (s, et, t) in edges
        if s in gid and t in gid
    ]
    return {"data": {"nodes": nodes, "edges": rendered_edges}}


def tier_zero_payload() -> dict[str, Any]:
    """Graph response for the Tier Zero seed query (nodes only)."""
    return _graph([oid for oid, (_n, _k, tz) in NODES.items() if tz], [])


def inbound_payload(object_ids: list[str]) -> dict[str, Any]:
    """Graph response: all edges pointing INTO the requested objectids (+ nodes)."""
    wanted = set(object_ids)
    hits = [(s, et, t) for (s, et, t) in EDGES if t in wanted]
    involved = [t for (_s, _e, t) in hits] + [s for (s, _e, _t) in hits]
    return _graph(involved, hits)


_IN_LIST_RE = re.compile(r"b\.objectid\s+IN\s+\[([^\]]*)\]", re.IGNORECASE)
_QUOTED_RE = re.compile(r"'((?:[^'\\]|\\.)*)'")


def synthetic_cypher_response(query: str) -> dict[str, Any] | None:
    """Serve a synthetic graph for seed/expansion queries; ``None`` otherwise.

    Returning ``None`` lets the mock fall back to the static cypher fixture, so
    ordinary ``bhe cypher`` calls keep their canned behaviour.
    """
    if not query:
        return None
    in_match = _IN_LIST_RE.search(query)
    if in_match:
        ids = [m.group(1).replace("\\'", "'") for m in _QUOTED_RE.finditer(in_match.group(1))]
        return inbound_payload(ids)
    if "admin_tier_0" in query and "MATCH (n)" in query:
        return tier_zero_payload()
    return None


# Per-domain finding aggregates the mock's ``/details`` endpoint serves, keyed by
# finding type, so ``bhe --mock triage`` / ``findings`` exercise real per-type
# ranking against the BHE-shaped ``{count, data:[{Severity, ImpactPercentage}]}``
# envelope (the same estate is served for every domain).
DETAILS: dict[str, dict[str, Any]] = {
    "DCSync": {"severity": "critical", "count": 2, "impact": 0.91},
    "Kerberoasting": {"severity": "high", "count": 3, "impact": 0.34},
    "ASREPRoasting": {"severity": "medium", "count": 1, "impact": 0.12},
}


def available_types_payload() -> dict[str, Any]:
    """Response for a domain's available finding types (``available-types``)."""
    return {"data": list(DETAILS)}


def details_payload(finding: str | None) -> dict[str, Any]:
    """BHE-shaped ``/details`` envelope for one finding type (or an empty page)."""
    spec = DETAILS.get(finding or "")
    if spec is None:
        return {"count": 0, "limit": 1, "skip": 0, "data": []}
    return {
        "count": spec["count"],
        "limit": 1,
        "skip": 0,
        "data": [
            {
                "Finding": finding,
                "Severity": spec["severity"],
                "ImpactPercentage": spec["impact"],
                "Accepted": False,
                "PrincipalName": f"SAMPLE-{finding}@CORP.LOCAL",
                "Props": {"name": f"SAMPLE-{finding}@CORP.LOCAL"},
            }
        ],
    }

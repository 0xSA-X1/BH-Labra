"""Read-only Cypher builders + a saved-query catalog for the TUI.

Seeded from the proven Pathstrike query builders, decoupled from Pathstrike's
edge-handler registry so they stand alone.  BloodHound does not support
parameterised Cypher, so values are inlined and escaped via :func:`escape`.

All builders here are read-only by construction; they are additionally screened
by :func:`bhe.api.readonly.assert_cypher_readonly` before execution.
"""

from __future__ import annotations


def escape(value: str) -> str:
    """Escape a string for safe inline use in a single-quoted Cypher literal."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def shortest_path(source_id: str, target_id: str) -> str:
    """Shortest path between two principals, matched by objectid.

    ``bhe hunt`` resolves friendly names to objectids first (handling partial
    names, raw ids and disambiguation), so the builders match the exact node by
    its indexed ``objectid`` rather than a possibly-ambiguous name.
    """
    return (
        "MATCH p=shortestPath((s)-[*1..]->(t)) "
        f"WHERE s.objectid = '{escape(source_id)}' AND t.objectid = '{escape(target_id)}' "
        "RETURN p"
    )


def all_shortest_paths(source_id: str, target_id: str) -> str:
    """All equally-short paths between two principals, matched by objectid."""
    return (
        "MATCH p=allShortestPaths((s)-[*1..]->(t)) "
        f"WHERE s.objectid = '{escape(source_id)}' AND t.objectid = '{escape(target_id)}' "
        "RETURN p"
    )


def path_to_tier_zero(source_id: str, all_paths: bool = False) -> str:
    """Shortest path from a principal (by objectid) to *any* Tier Zero node.

    The target set is identified by BHE's analysis tags rather than a fixed node,
    so it adapts to whatever the tenant marks as Tier Zero.
    """
    finder = "allShortestPaths" if all_paths else "shortestPath"
    return (
        f"MATCH p={finder}((s)-[*1..]->(t)) "
        f"WHERE s.objectid = '{escape(source_id)}' "
        "AND (coalesce(t.system_tags,'') CONTAINS 'admin_tier_0' OR t.isTierZero = true) "
        "RETURN p"
    )


# Friendly platform name -> the node-label PREFIX BloodHound/OpenGraph uses for
# that platform's nodes.  Azure is built in (``AZ``); Okta/GitHub/Jamf come from
# OpenGraph collectors (e.g. Okta uses ``Okta_*`` kinds).
PLATFORM_PREFIXES: dict[str, str] = {
    "azure": "AZ",
    "entra": "AZ",
    "az": "AZ",
    "okta": "Okta",
    "github": "GitHub",
    "jamf": "Jamf",
}

# Default hybrid targets: every non-AD platform we know about, so a single
# ``hunt hybrid`` surfaces Azure *and* OpenGraph (Okta/GitHub/Jamf) crossings.
DEFAULT_HYBRID_PREFIXES: tuple[str, ...] = ("AZ", "Okta", "GitHub", "Jamf")


def cross_platform_path(
    source_id: str, prefixes: tuple[str, ...] | list[str], all_paths: bool = False
) -> str:
    """Shortest path from an AD principal (by objectid) to *any* non-AD platform node.

    Matches the target by node-label prefix, so it finds a hybrid path to Azure or
    any OpenGraph platform (Okta/GitHub/Jamf, or a custom one) regardless of which
    specific edge crosses the on-prem -> cloud/SaaS boundary.
    """
    finder = "allShortestPaths" if all_paths else "shortestPath"
    quoted = ", ".join(f"'{escape(p)}'" for p in prefixes)
    return (
        f"MATCH p={finder}((s)-[*1..]->(t)) "
        f"WHERE s.objectid = '{escape(source_id)}' "
        f"AND any(lbl IN labels(t) WHERE any(pfx IN [{quoted}] WHERE lbl STARTS WITH pfx)) "
        "RETURN p"
    )


def hybrid_path_to_azure(source_id: str, all_paths: bool = False) -> str:
    """Shortest path from an on-prem AD principal (by objectid) to *any* Azure node."""
    return cross_platform_path(source_id, ("AZ",), all_paths=all_paths)


def node_by_name(name: str) -> str:
    """Resolve a full node by its (case-insensitive) name."""
    return (
        f"MATCH (n) WHERE toUpper(n.name) = '{escape(name.upper())}' RETURN n LIMIT 1"
    )


def kerberoastable_users(domain: str) -> str:
    """Enabled users with an SPN (Kerberoastable) in a domain."""
    return (
        f"MATCH (u:User) WHERE u.domain = '{escape(domain.upper())}' "
        f"AND u.hasspn = true AND u.enabled = true RETURN u"
    )


def asrep_roastable_users(domain: str) -> str:
    """Enabled users with DontReqPreauth (AS-REP roastable) in a domain."""
    return (
        f"MATCH (u:User) WHERE u.domain = '{escape(domain.upper())}' "
        f"AND u.dontreqpreauth = true AND u.enabled = true RETURN u"
    )


def domain_trusts() -> str:
    """All inter-domain trust relationships."""
    return "MATCH p=(d1:Domain)-[r]->(d2:Domain) RETURN p"


def tier_zero_principals() -> str:
    """All Tier Zero / high-value principals."""
    return (
        "MATCH (n) WHERE coalesce(n.system_tags,'') CONTAINS 'admin_tier_0' "
        "OR n.isTierZero = true RETURN n"
    )


def high_value_nodes(domain: str) -> str:
    """High-value targets in a domain (admincount/Tier Zero/Domain)."""
    return (
        f"MATCH (t) WHERE (t.admincount = true OR t.isTierZero = true OR t:Domain) "
        f"AND t.domain = '{escape(domain.upper())}' "
        f"RETURN t.name AS name, t.objectid AS objectid, labels(t) AS labels"
    )


def owned_principals() -> str:
    """Principals marked 'owned' (if your tenant tags them)."""
    return (
        "MATCH (n) WHERE coalesce(n.system_tags,'') CONTAINS 'owned' "
        "OR n.owned = true RETURN n"
    )


# Named saved queries surfaced as quick-picks in the Cypher console.  Entries
# whose builder needs an argument expose a ``{domain}`` placeholder the screen
# fills in interactively.
SAVED_QUERIES: dict[str, str] = {
    "Tier Zero principals": tier_zero_principals(),
    "Domain trusts": domain_trusts(),
    "Owned principals": owned_principals(),
    "Kerberoastable users (domain)": kerberoastable_users("{domain}"),
    "AS-REP roastable users (domain)": asrep_roastable_users("{domain}"),
    "High-value nodes (domain)": high_value_nodes("{domain}"),
    "All users (sample)": "MATCH (u:User) RETURN u LIMIT 100",
    "All computers (sample)": "MATCH (c:Computer) RETURN c LIMIT 100",
}

"""Tests for the backward-scoping engine, its Cypher adapter, and scale safety."""

from __future__ import annotations

from bhe.api.readonly import assert_cypher_readonly
from bhe.scope import (
    Edge,
    Expansion,
    backward_reach,
    edges_from_response,
    inbound_query,
    make_expander,
    rank_choke_points,
    seeds_from_response,
    tier_zero_seed_query,
)

# An in-memory funnel: target -> list of (source_oid, name, kind, edge_type).
_FUNNEL = {
    "DA": [
        ("HELP", "HELPDESK", "Group", "GenericAll"),
        ("BACKUP", "BACKUP-SVC", "User", "DCSync"),
        ("DEV", "DEV-ADMIN", "User", "GenericAll"),
    ],
    "HELP": [
        ("AU", "AUTH USERS", "Group", "MemberOf"),
        ("ALICE", "ALICE", "User", "MemberOf"),
        ("BOB", "BOB", "User", "MemberOf"),
    ],
}


def _static_expander(inbound: dict, *, threshold: int = 50):
    """Mimic the adapter: enumerate small targets, truncate mass ones."""

    async def expand(frontier):
        edges: list[Edge] = []
        truncated: dict[str, int] = {}
        for target in frontier:
            group = inbound.get(target, [])
            if len(group) > threshold:
                truncated[target] = len(group)
                continue
            for src, name, kind, etype in group:
                edges.append(Edge(src, name, kind, etype, target))
        return Expansion(edges, truncated)

    return expand


async def test_backward_reach_builds_layers() -> None:
    snap = await backward_reach({"DA": ("DOMAIN ADMINS", "Group")}, _static_expander(_FUNNEL))
    assert set(snap.nodes) == {"DA", "HELP", "BACKUP", "DEV", "AU", "ALICE", "BOB"}
    assert snap.nodes["DA"].depth == 0
    assert snap.nodes["HELP"].depth == 1
    assert snap.nodes["ALICE"].depth == 2
    assert len(snap.edges) == 6


async def test_choke_ranking_finds_dominant_node() -> None:
    snap = await backward_reach({"DA": ("DOMAIN ADMINS", "Group")}, _static_expander(_FUNNEL))
    chokes, baseline = rank_choke_points(snap)
    assert baseline == 5  # AU, ALICE, BOB, BACKUP, DEV all reach DA
    assert chokes[0].name == "HELPDESK"
    assert chokes[0].principals_cut == 3   # cuts AU + ALICE + BOB
    assert round(chokes[0].pct_cut, 2) == 0.6
    # BACKUP/DEV are independent direct paths -> no other single-node choke.
    assert len(chokes) == 1


async def test_diamond_has_no_false_choke() -> None:
    # S reaches T0 via two disjoint intermediates: neither is a choke point.
    diamond = {
        "T0": [("A", "A", "Group", "AdminTo"), ("B", "B", "Group", "AdminTo")],
        "A": [("S", "S", "User", "MemberOf")],
        "B": [("S", "S", "User", "MemberOf")],
    }
    snap = await backward_reach({"T0": ("T0", "Group")}, _static_expander(diamond))
    chokes, baseline = rank_choke_points(snap)
    assert baseline == 1
    # A and B each carry S, but removing either leaves the other path -> no choke.
    assert chokes == []


async def test_mass_node_is_truncated_not_enumerated() -> None:
    mass = {
        "DA": [("HELP", "HELPDESK", "Group", "GenericAll")],
        "HELP": [(f"u{i}", f"U{i}", "User", "MemberOf") for i in range(5000)],
    }
    snap = await backward_reach(
        {"DA": ("DA", "Group")}, _static_expander(mass, threshold=50)
    )
    assert "HELP" in snap.truncated
    assert snap.nodes["HELP"].truncated
    assert snap.nodes["HELP"].fan_in == 5000
    assert "u0" not in snap.nodes          # the 5000 sources were NOT enumerated
    _chokes, baseline = rank_choke_points(snap)
    assert baseline == 5000                 # but they're counted as exposure


async def test_max_nodes_cap_sets_hit_limit() -> None:
    wide = {"DA": [(f"n{i}", f"N{i}", "User", "AdminTo") for i in range(100)]}
    # threshold high so the 100 are enumerated (not truncated); cap stops us.
    snap = await backward_reach(
        {"DA": ("DA", "Group")}, _static_expander(wide, threshold=1000), max_nodes=10
    )
    assert snap.hit_limit
    assert len(snap.nodes) <= 11  # seed + up to the cap


def test_inbound_query_is_bounded_and_readonly() -> None:
    q = inbound_query(["S-1-5-21-x-512", "S-1-5-21-x-1140"], limit=2000)
    assert "b.objectid IN [" in q
    assert "LIMIT 2000" in q
    assert_cypher_readonly(q)  # must not raise
    assert_cypher_readonly(tier_zero_seed_query())


def test_edges_and_seeds_from_synthetic_payload() -> None:
    from bhe.api._synthetic import NODES, inbound_payload, tier_zero_payload

    da = next(oid for oid, (_n, _k, tz) in NODES.items() if tz)
    edges = edges_from_response(inbound_payload([da]))
    assert {e.source_name for e in edges} >= {"HELPDESK@CORP.LOCAL", "BACKUP-SVC@CORP.LOCAL"}
    assert all(e.target == da for e in edges)

    seeds = seeds_from_response(tier_zero_payload())
    assert da in seeds and "DOMAIN ADMINS" in seeds[da][0]


async def test_make_expander_enumerates_then_truncates() -> None:
    from bhe.api._synthetic import NODES
    from bhe.api.client import BHEClient

    da = next(oid for oid, (_n, _k, tz) in NODES.items() if tz)
    help_oid = next(oid for oid, (n, _k, _t) in NODES.items() if "HELPDESK" in n)
    async with BHEClient.connect("https://mock", "id", "key", mock=True) as client:
        # Generous threshold: Domain Admins' 3 inbound controllers are enumerated.
        big = await make_expander(client, threshold=50)({da})
        assert {e.source_name for e in big.edges} >= {
            "HELPDESK@CORP.LOCAL", "BACKUP-SVC@CORP.LOCAL", "DEV-ADMIN@DEV.CORP.LOCAL"
        }
        assert not big.truncated

        # Tight threshold: 3 inbound exceeds it -> truncated, NOT enumerated.
        small = await make_expander(client, threshold=2)({da})
        assert da in small.truncated
        assert small.edges == []

        # Saturation split: a 2-node batch that overflows the cap must split so
        # each mass node is identified individually (threshold=1 -> cap small).
        both = await make_expander(client, threshold=1)({da, help_oid})
        assert da in both.truncated and help_oid in both.truncated
        assert both.edges == []

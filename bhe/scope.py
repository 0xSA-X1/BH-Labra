"""Backward attack-path scoping: build the Tier-Zero-reachable sub-graph, cheaply.

This is the engine behind ``bhe choke`` / ``leaks`` / ``map``.  It implements the
funnel insight: instead of asking the database for global paths (which times
out), we **seed at Tier Zero and walk backward one inbound hop at a time** via
bounded, batched queries.  Because Tier Zero is the narrow end of the funnel the
frontier converges, and the reachable sub-graph (:class:`Snapshot`) is small
enough to run real graph algorithms on client-side.

Scale notes (why this survives a multi-million-object tenant):

* **Expansion is bounded, never blanket-LIMITed.** :func:`make_expander` requests
  at most ``batch x (threshold+1)`` rows; if a query saturates it splits the
  batch until every node is either fully enumerated or provably a *mass*
  node — which is then **truncated** (its huge fan-in recorded, its sources NOT
  enumerated).  So a 2-million-member group costs one bounded query, not a
  meltdown, and no edges are ever silently dropped.
* **Choke ranking is near-linear** (:func:`rank_choke_points` via a dominator
  tree), not the exponential "remove-each-node-and-recount" greedy.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from bhe.parsing.graph import parse_graph
from bhe.queries.library import escape


@dataclass(frozen=True)
class Edge:
    """A directed control/abuse edge ``source -> target`` (target nearer T0)."""

    source: str
    source_name: str
    source_kind: str
    edge_type: str
    target: str
    source_domain: str = ""


@dataclass
class Expansion:
    """Result of one backward hop: enumerated edges + truncated mass nodes."""

    edges: list[Edge]
    truncated: dict[str, int]  # objectid -> (approx) inbound fan-in


# An ``expand`` takes a frontier of objectids and returns one hop of inbound
# results (edges whose target is in the frontier, plus any mass nodes truncated).
ExpandFn = Callable[[set[str]], Awaitable[Expansion]]


@dataclass
class Node:
    """A node in the reachable sub-graph, with its distance from the seed set."""

    objectid: str
    name: str
    kind: str
    depth: int            # hops back from Tier Zero (0 = seed)
    domain: str = ""
    fan_in: int = 0       # edges pointing into it within the snapshot
    truncated: bool = False  # a high-fan-in mass aggregator we stopped expanding


@dataclass
class Snapshot:
    """The Tier-Zero-reachable sub-graph produced by :func:`backward_reach`."""

    seeds: set[str]
    nodes: dict[str, Node]
    edges: list[Edge]
    rounds: int
    truncated: list[str] = field(default_factory=list)
    hit_limit: bool = False


@dataclass
class ChokePoint:
    """A node whose removal disconnects many sources from the seed set."""

    objectid: str
    name: str
    kind: str
    depth: int
    principals_cut: int   # weighted sources disconnected by removing this node
    pct_cut: float        # fraction of the originally-exposed population


# ----------------------------------------------------------------------------
# Pure engine: backward BFS
# ----------------------------------------------------------------------------


def _seed_meta(meta: tuple) -> tuple[str, str, str]:
    """Normalise a seed tuple to ``(name, kind, domain)`` (domain optional)."""
    name = meta[0] if len(meta) > 0 else ""
    kind = meta[1] if len(meta) > 1 else ""
    domain = meta[2] if len(meta) > 2 else ""
    return name, kind, domain


async def backward_reach(
    seeds: dict[str, tuple],
    expand: ExpandFn,
    *,
    max_depth: int = 4,
    max_nodes: int = 20000,
) -> Snapshot:
    """Walk backward from ``seeds`` one inbound hop at a time.

    Args:
        seeds: objectid -> (name, kind[, domain]) for the Tier Zero / target set.
        expand: async callback returning one hop of inbound results (the adapter
            owns the mass-node threshold; see :func:`make_expander`).
        max_depth: stop after this many hops (real attack paths are short).
        max_nodes: hard cap on snapshot size (runaway-frontier safety).
    """
    nodes: dict[str, Node] = {}
    for oid, meta in seeds.items():
        name, kind, domain = _seed_meta(meta)
        nodes[oid] = Node(oid, name, kind, depth=0, domain=domain)

    edges: list[Edge] = []
    truncated: list[str] = []
    seen_edges: set[tuple[str, str, str]] = set()
    frontier: set[str] = set(seeds)
    visited: set[str] = set(seeds)
    rounds = 0
    hit_limit = False

    for depth in range(1, max_depth + 1):
        if not frontier or hit_limit:
            break
        rounds = depth
        result = await expand(frontier)

        # Record mass aggregators: count noted, sources deliberately NOT walked.
        for target, count in result.truncated.items():
            node = nodes.get(target)
            if node is not None:
                node.truncated = True
                node.fan_in = max(node.fan_in, count)
            if target not in truncated:
                truncated.append(target)

        next_frontier: set[str] = set()
        for e in result.edges:
            if e.target not in frontier:
                continue
            key = (e.source, e.edge_type, e.target)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            edges.append(e)
            if e.source not in nodes:
                nodes[e.source] = Node(
                    e.source, e.source_name, e.source_kind, depth=depth,
                    domain=e.source_domain,
                )
                if e.source not in visited:
                    next_frontier.add(e.source)
            if len(nodes) >= max_nodes:
                hit_limit = True
                break

        visited |= next_frontier
        frontier = next_frontier

    incoming: dict[str, int] = defaultdict(int)
    for e in edges:
        incoming[e.target] += 1
    for node in nodes.values():
        if not node.truncated:
            node.fan_in = incoming.get(node.objectid, 0)

    return Snapshot(set(seeds), nodes, edges, rounds, truncated, hit_limit)


# ----------------------------------------------------------------------------
# Pure engine: choke points via a dominator tree (near-linear)
# ----------------------------------------------------------------------------


def _dominators(succ: dict[str, list[str]], entry: str) -> dict[str, str]:
    """Immediate-dominator tree of the flowgraph ``succ`` rooted at ``entry``.

    Cooper-Harvey-Kennedy "A Simple, Fast Dominance Algorithm" — O(N*E) worst
    case but a couple of passes in practice, on the *small* reachable sub-graph.
    """
    # Iterative DFS postorder over nodes reachable from entry.
    order: list[str] = []
    visited = {entry}
    stack = [(entry, iter(succ.get(entry, [])))]
    while stack:
        node, it = stack[-1]
        advanced = False
        for child in it:
            if child not in visited:
                visited.add(child)
                stack.append((child, iter(succ.get(child, []))))
                advanced = True
                break
        if not advanced:
            order.append(node)
            stack.pop()

    postnum = {n: i for i, n in enumerate(order)}  # entry has the highest number
    preds: dict[str, list[str]] = defaultdict(list)
    for n in visited:
        for s in succ.get(n, []):
            if s in visited:
                preds[s].append(n)

    idom: dict[str, str] = {entry: entry}

    def intersect(a: str, b: str) -> str:
        while a != b:
            while postnum[a] < postnum[b]:
                a = idom[a]
            while postnum[b] < postnum[a]:
                b = idom[b]
        return a

    rpo = list(reversed(order))
    changed = True
    while changed:
        changed = False
        for n in rpo:
            if n == entry:
                continue
            new_idom: str | None = None
            for p in preds[n]:
                if p in idom:
                    new_idom = p if new_idom is None else intersect(p, new_idom)
            if new_idom is not None and idom.get(n) != new_idom:
                idom[n] = new_idom
                changed = True
    return idom


def rank_choke_points(snapshot: Snapshot, *, max_points: int = 25) -> tuple[list[ChokePoint], int]:
    """Rank nodes by how many exposed sources their removal disconnects from T0.

    Uses a dominator tree: a node that dominates a source lies on *every* path
    from that source to Tier Zero, so removing it cuts that source.  Each
    source's dominator chain is exactly its ordered list of choke points; we
    tally source weight along those chains.  Near-linear, and stable.

    Returns ``(choke_points, baseline)`` where ``baseline`` is the total weighted
    source population that reaches the seed set.
    """
    seeds = snapshot.seeds
    if not snapshot.edges:
        return [], 0

    # H = reversed funnel: entry -> seeds -> ... -> sources.
    entry = "\x00entry"
    succ: dict[str, list[str]] = defaultdict(list)
    forward_targets: set[str] = set()
    for e in snapshot.edges:
        succ[e.target].append(e.source)   # reverse the forward edge
        forward_targets.add(e.target)
    succ[entry] = [s for s in seeds if s in snapshot.nodes]
    if not succ[entry]:
        return [], 0

    # Sources = funnel entry points (never a forward target) or truncated mass
    # aggregators (weighted by the population they stand in for).
    sources: dict[str, int] = {}
    for oid, node in snapshot.nodes.items():
        if oid in seeds:
            continue
        if node.truncated:
            sources[oid] = max(node.fan_in, 1)
        elif oid not in forward_targets:
            sources[oid] = 1
    baseline = sum(sources.values())
    if baseline == 0:
        return [], 0

    idom = _dominators(succ, entry)

    # Tally source weight up each source's dominator chain (its choke points).
    tally: dict[str, int] = defaultdict(int)
    for src, weight in sources.items():
        x = idom.get(src)
        guard = 0
        while x is not None and x != entry and guard <= len(snapshot.nodes):
            if x not in seeds and x != src:
                tally[x] += weight
            nxt = idom.get(x)
            if nxt is None or nxt == x:
                break
            x = nxt
            guard += 1

    ranked = sorted(tally.items(), key=lambda kv: kv[1], reverse=True)
    picks: list[ChokePoint] = []
    for oid, cut in ranked[:max_points]:
        if cut <= 0:
            break
        node = snapshot.nodes[oid]
        picks.append(
            ChokePoint(oid, node.name, node.kind, node.depth, cut, cut / baseline)
        )
    return picks, baseline


# ----------------------------------------------------------------------------
# Adapter: turn `expand` into real, bounded Cypher
# ----------------------------------------------------------------------------


def tier_zero_seed_query(limit: int = 2000) -> str:
    """Cypher returning the Tier Zero / high-value node set (the BFS seeds)."""
    return (
        "MATCH (n) WHERE coalesce(n.system_tags,'') CONTAINS 'admin_tier_0' "
        f"OR n.isTierZero = true RETURN n LIMIT {limit}"
    )


def inbound_query(object_ids: list[str], edge_types: list[str] | None = None, limit: int = 2000) -> str:
    """One bounded inbound hop: who points AT this batch of nodes (indexed IN-list)."""
    ids = ", ".join(f"'{escape(o)}'" for o in object_ids)
    query = f"MATCH (a)-[r]->(b) WHERE b.objectid IN [{ids}]"
    if edge_types:
        ets = ", ".join(f"'{escape(t)}'" for t in edge_types)
        query += f" AND type(r) IN [{ets}]"
    query += f" RETURN a, r, b LIMIT {limit}"
    return query


def _node_oid(node) -> str | None:
    return node.object_id or (node.properties or {}).get("objectid") or node.link_id


def edges_from_response(response: dict) -> list[Edge]:
    """Parse a BHE graph response into :class:`Edge` objects keyed by objectid."""
    nodes, raw_edges = parse_graph(response)
    by_link = {n.link_id: n for n in nodes if n.link_id}
    out: list[Edge] = []
    for edge in raw_edges:
        src = by_link.get(edge.source)
        tgt = by_link.get(edge.target)
        if src is None or tgt is None:
            continue
        src_oid = _node_oid(src)
        tgt_oid = _node_oid(tgt)
        if not src_oid or not tgt_oid:
            continue
        props = src.properties or {}
        out.append(
            Edge(
                source=src_oid,
                source_name=props.get("name") or src.label or src_oid,
                source_kind=src.kind or src.label or "",
                edge_type=edge.kind or edge.label or "",
                target=tgt_oid,
                source_domain=props.get("domain", ""),
            )
        )
    return out


# Machine-scoped kinds whose names are ``label@HOST.DOMAIN`` / ``HOST.DOMAIN``
# (BHE often leaves their ``domain`` property blank), so we drop the host label.
_LOCAL_KINDS = {"adlocalgroup", "localgroup", "localuser", "computer"}


def domain_of(name: str, kind: str = "", domain: str = "") -> str:
    """Best-effort domain for a node, used to bucket cross-domain crossings.

    Prefers BHE's ``domain`` property.  When it's blank - which is exactly why
    ``leaks`` used to show ``?`` for local-group/-user and computer-local objects
    that don't carry one - we derive it from the node name: the suffix after
    ``@`` (or the DNS suffix of a bare FQDN), dropping the leading host label for
    machine-scoped kinds.  Returns ``"(unknown)"`` when nothing usable is present,
    so unrelated domain-less nodes aren't all merged under one ``?`` bucket.
    """
    if domain and domain.strip():
        return domain.strip()
    n = (name or "").strip()
    suffix = (n.rsplit("@", 1)[1] if "@" in n else n).strip().strip(".")
    if "." not in suffix:
        return "(unknown)"
    labels = suffix.split(".")
    if (kind or "").lower() in _LOCAL_KINDS and len(labels) > 2:
        labels = labels[1:]  # strip the host label off HOST.DOMAIN
    return ".".join(labels).upper()


def seeds_in_domain(
    seeds: dict[str, tuple], aliases: set[str]
) -> dict[str, tuple[str, ...]]:
    """Keep only seeds whose domain matches one of ``aliases`` (already lowercased).

    ``aliases`` is typically ``{domain_name, domain_sid}`` from the resolved
    domain, so a Tier-Zero seed set can be narrowed to one domain for "work one
    domain at a time" scoping.  Seeds with no domain are dropped under a filter.
    """
    return {
        oid: meta
        for oid, meta in seeds.items()
        if (meta[2] if len(meta) > 2 else "").strip().lower() in aliases
    }


def seeds_from_response(response: dict) -> dict[str, tuple[str, str, str]]:
    """Parse a node-returning graph response into ``{objectid: (name, kind, domain)}``."""
    nodes, _ = parse_graph(response)
    seeds: dict[str, tuple[str, str, str]] = {}
    for node in nodes:
        oid = _node_oid(node)
        if not oid:
            continue
        props = node.properties or {}
        seeds[oid] = (
            props.get("name") or node.label or oid,
            node.kind or node.label or "",
            props.get("domain", ""),
        )
    return seeds


def make_expander(
    client,
    *,
    edge_types: list[str] | None = None,
    threshold: int = 50,
    chunk: int = 100,
    concurrency: int = 8,
) -> ExpandFn:
    """Build a bounded, mass-safe, concurrent async ``expand`` over a client.

    Each query fetches at most ``len(batch) * (threshold+1)`` rows.  If a query
    saturates that cap, the batch is split (down to a single node) so we can tell
    *which* node is the mass aggregator and truncate it without ever enumerating
    its sources — the key to surviving high-degree nodes on a large tenant.

    A frontier is fanned out across up to ``concurrency`` in-flight queries (a
    semaphore bounds it so we don't trip the tenant's rate limiter), since the
    per-chunk probes are independent.  Query count scales with the small
    Tier-Zero-reachable sub-graph, not the size of the tenant.
    """
    sem = asyncio.Semaphore(concurrency)

    async def _probe(ids: list[str]) -> tuple[list[Edge], dict[str, int]]:
        if not ids:
            return [], {}
        cap = len(ids) * (threshold + 1)
        async with sem:
            response = await client.cypher_query(
                inbound_query(ids, edge_types, limit=cap + 1)
            )
        batch_edges = edges_from_response(response)
        saturated = len(batch_edges) > cap

        if saturated and len(ids) > 1:
            mid = len(ids) // 2
            left, right = await asyncio.gather(_probe(ids[:mid]), _probe(ids[mid:]))
            return left[0] + right[0], {**left[1], **right[1]}

        by_target: dict[str, list[Edge]] = defaultdict(list)
        for e in batch_edges:
            by_target[e.target].append(e)
        edges: list[Edge] = []
        truncated: dict[str, int] = {}
        for oid in ids:
            group = by_target.get(oid, [])
            if len(group) > threshold or (saturated and len(ids) == 1):
                # Mass aggregator: record the (floor of) fan-in, don't enumerate.
                truncated[oid] = max(len(group), threshold + 1)
            else:
                edges.extend(group)
        return edges, truncated

    async def expand(frontier: set[str]) -> Expansion:
        ids = list(frontier)
        chunks = [ids[i : i + chunk] for i in range(0, len(ids), chunk)]
        results = await asyncio.gather(*(_probe(c) for c in chunks))
        edges: list[Edge] = []
        truncated: dict[str, int] = {}
        for chunk_edges, chunk_trunc in results:
            edges.extend(chunk_edges)
            truncated.update(chunk_trunc)
        return Expansion(edges, truncated)

    return expand

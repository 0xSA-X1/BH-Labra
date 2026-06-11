"""Turn BHE graph/Cypher JSON into nodes, edges and (best-effort) paths.

BHE's Cypher endpoint returns a single flat graph regardless of how many paths
matched::

    {"data": {"nodes": {"<id>": {...}}, "edges": [{"source": "<id>", ...}]}}

Scalar-returning queries instead yield a ``data`` list/dict of literals, which
:func:`extract_literals` surfaces as plain rows.
"""

from __future__ import annotations

from typing import Any

from bhe.api.models import GraphEdge, GraphNode, Path


def _graph_payload(response: dict[str, Any]) -> dict[str, Any]:
    """Drill into the ``data`` envelope when present."""
    if isinstance(response, dict) and isinstance(response.get("data"), dict):
        return response["data"]
    return response if isinstance(response, dict) else {}


def parse_graph(response: dict[str, Any]) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Parse a BHE graph response into node and edge models.

    Args:
        response: Raw JSON from the Cypher or pathfinding endpoints.

    Returns:
        ``(nodes, edges)`` - ``nodes`` keyed order preserved as returned.
    """
    payload = _graph_payload(response)
    raw_nodes = payload.get("nodes") or {}
    raw_edges = payload.get("edges") or []

    nodes: list[GraphNode] = []
    if isinstance(raw_nodes, dict):
        for node_id, node in raw_nodes.items():
            data = dict(node) if isinstance(node, dict) else {}
            # The map key is what edges reference; keep it for path linking.
            data["graph_id"] = str(node_id)
            data.setdefault("objectId", data.get("objectId") or node_id)
            nodes.append(GraphNode.model_validate(data))
    elif isinstance(raw_nodes, list):
        nodes = [GraphNode.model_validate(n) for n in raw_nodes if isinstance(n, dict)]

    edges = [GraphEdge.model_validate(e) for e in raw_edges if isinstance(e, dict)]
    return nodes, edges


def nodes_table(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a graph response into display rows (objectid, kind, name).

    Pulls the most useful property fields out of each node for a table view.
    """
    nodes, _ = parse_graph(response)
    rows: list[dict[str, Any]] = []
    for node in nodes:
        props = node.properties or {}
        rows.append(
            {
                "objectid": node.object_id or props.get("objectid", ""),
                "kind": node.kind or node.label or "",
                "name": props.get("name") or props.get("displayname") or "",
                "domain": props.get("domain", ""),
                "enabled": props.get("enabled", ""),
            }
        )
    return rows


def extract_literals(response: dict[str, Any]) -> list[Any]:
    """Return scalar literals from a non-graph Cypher response.

    Handles both ``{"data": [...]}`` and ``{"data": {"<col>": [...]}}`` shapes.
    """
    if not isinstance(response, dict):
        return []
    data = response.get("data", response)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # A graph payload isn't "literals" - skip it.
        if "nodes" in data or "edges" in data:
            return []
        flattened: list[Any] = []
        for value in data.values():
            if isinstance(value, list):
                flattened.extend(value)
            else:
                flattened.append(value)
        return flattened
    return []


def reconstruct_paths(
    nodes: list[GraphNode], edges: list[GraphEdge]
) -> list[Path]:
    """Best-effort reconstruction of discrete paths from a flat graph.

    Walks the directed edge set from every source node (in-degree 0) to every
    reachable sink, emitting one :class:`Path` per simple chain.  This is exact
    for the common case of a small attack-path graph and degrades gracefully
    (it stops at cycles and node revisits) for denser graphs.

    Args:
        nodes: Parsed graph nodes.
        edges: Parsed graph edges.

    Returns:
        A list of reconstructed paths, longest first.
    """
    by_id = {n.link_id: n for n in nodes if n.link_id}
    if not by_id or not edges:
        return []

    adjacency: dict[str, list[GraphEdge]] = {}
    indegree: dict[str, int] = {nid: 0 for nid in by_id}
    for edge in edges:
        if edge.source not in by_id or edge.target not in by_id:
            continue
        adjacency.setdefault(edge.source, []).append(edge)
        indegree[edge.target] = indegree.get(edge.target, 0) + 1

    sources = [nid for nid, deg in indegree.items() if deg == 0] or list(by_id)
    paths: list[Path] = []

    def walk(node_id: str, visited: set[str], chain_nodes: list[GraphNode],
             chain_edges: list[GraphEdge]) -> None:
        outgoing = adjacency.get(node_id, [])
        if not outgoing:
            if chain_edges:
                paths.append(Path(nodes=list(chain_nodes), edges=list(chain_edges)))
            return
        for edge in outgoing:
            target = edge.target
            if target in visited or target not in by_id:
                # Terminate the chain here rather than loop.
                paths.append(Path(nodes=list(chain_nodes), edges=list(chain_edges)))
                continue
            visited.add(target)
            chain_nodes.append(by_id[target])
            chain_edges.append(edge)
            walk(target, visited, chain_nodes, chain_edges)
            chain_edges.pop()
            chain_nodes.pop()
            visited.discard(target)

    for src in sources:
        if src not in by_id:
            continue
        walk(src, {src}, [by_id[src]], [])

    paths.sort(key=lambda p: p.length(), reverse=True)
    return paths

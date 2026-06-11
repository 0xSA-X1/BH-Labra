"""Condensed-graph rendering for ``bhe map``.

Turns a :class:`~bhe.scope.Snapshot` into a Mermaid or Graphviz/DOT diagram of
the attack funnel, with two condensations that keep it readable at scale:

* **Choke points and Tier Zero are styled** so the eye lands on what to fix.
* **Leaf sources are bundled.** When many single-hop entry principals point at
  the same node, they collapse into one ``(N principals)`` meta-node instead of
  N separate lines — the "synthetic representation" of a path bundle.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from bhe.scope import Snapshot


def _condense(snapshot: Snapshot, choke_ids: set[str], bundle_threshold: int):
    """Return (nodes, bundles, edges) after collapsing leaf-source fan-in.

    nodes:   list of (objectid, label, kind in {tierzero,choke,mass,normal})
    bundles: list of (bundle_id, parent_objectid, count)
    edges:   list of (source_id, target_id, label)
    """
    targets = {e.target for e in snapshot.edges}
    outdeg = Counter(e.source for e in snapshot.edges)

    # A leaf source: a funnel entry (never a target) that points at exactly one
    # node and isn't a seed — the bundle candidates.
    leaves_by_parent: dict[str, list] = defaultdict(list)
    for e in snapshot.edges:
        if e.source not in targets and e.source not in snapshot.seeds and outdeg[e.source] == 1:
            leaves_by_parent[e.target].append(e)
    bundled_parents = {
        p: es for p, es in leaves_by_parent.items() if len(es) >= bundle_threshold
    }
    hidden = {e.source for es in bundled_parents.values() for e in es}

    nodes = []
    for oid, node in snapshot.nodes.items():
        if oid in hidden:
            continue
        if oid in snapshot.seeds:
            kind = "tierzero"
        elif oid in choke_ids:
            kind = "choke"
        elif node.truncated:
            kind = "mass"
        else:
            kind = "normal"
        label = node.name or oid
        if node.truncated:
            label = f"{label} (~{node.fan_in} in)"
        nodes.append((oid, label, kind))

    bundles = [(f"bundle::{p}", p, len(es)) for p, es in bundled_parents.items()]

    edges = []
    for bid, _parent, count in bundles:
        edges.append((bid, _parent, f"{count}x"))
    for e in snapshot.edges:
        if e.source in hidden:
            continue
        edges.append((e.source, e.target, e.edge_type))

    return nodes, bundles, edges


def _ids(nodes, bundles):
    """Assign short, render-safe ids to every node/bundle objectid."""
    mapping: dict[str, str] = {}
    for oid, _label, _kind in nodes:
        mapping.setdefault(oid, f"n{len(mapping)}")
    for bid, _parent, _count in bundles:
        mapping.setdefault(bid, f"n{len(mapping)}")
    return mapping


def to_mermaid(snapshot: Snapshot, choke_ids: set[str], bundle_threshold: int = 8) -> str:
    """Render the snapshot as a Mermaid ``graph RL`` (sources -> Tier Zero)."""
    nodes, bundles, edges = _condense(snapshot, choke_ids, bundle_threshold)
    mid = _ids(nodes, bundles)

    lines = ["graph RL"]
    for oid, label, _kind in nodes:
        lines.append(f'  {mid[oid]}["{label}"]')
    for bid, _parent, count in bundles:
        lines.append(f'  {mid[bid]}["({count} principals)"]')
    for src, tgt, label in edges:
        if src in mid and tgt in mid:
            lines.append(f"  {mid[src]} -->|{label}| {mid[tgt]}")
    for oid, _label, kind in nodes:
        if kind in ("tierzero", "choke", "mass"):
            lines.append(f"  class {mid[oid]} {kind}")
    lines.append("  classDef tierzero fill:#b00020,color:#fff,stroke:#600")
    lines.append("  classDef choke fill:#f6a000,color:#000,stroke:#a06")
    lines.append("  classDef mass fill:#555,color:#fff,stroke:#222")
    return "\n".join(lines)


def to_dot(snapshot: Snapshot, choke_ids: set[str], bundle_threshold: int = 8) -> str:
    """Render the snapshot as Graphviz DOT (``rankdir=RL``)."""
    nodes, bundles, edges = _condense(snapshot, choke_ids, bundle_threshold)
    mid = _ids(nodes, bundles)
    fill = {
        "tierzero": "#b00020",
        "choke": "#f6a000",
        "mass": "#555555",
        "normal": "#dddddd",
    }
    fontcolor = {"tierzero": "white", "choke": "black", "mass": "white", "normal": "black"}

    lines = ["digraph attackpaths {", "  rankdir=RL;", '  node [style=filled,shape=box];']
    for oid, label, kind in nodes:
        safe = label.replace('"', "'")
        lines.append(
            f'  {mid[oid]} [label="{safe}",fillcolor="{fill[kind]}",fontcolor="{fontcolor[kind]}"];'
        )
    for bid, _parent, count in bundles:
        lines.append(
            f'  {mid[bid]} [label="({count} principals)",fillcolor="#eeeeee",shape=oval];'
        )
    for src, tgt, label in edges:
        if src in mid and tgt in mid:
            safe = label.replace('"', "'")
            lines.append(f'  {mid[src]} -> {mid[tgt]} [label="{safe}"];')
    lines.append("}")
    return "\n".join(lines)

"""Tests for condensed-graph rendering (bundling + styling)."""

from __future__ import annotations

from bhe.render import to_dot, to_mermaid
from bhe.scope import Edge, Node, Snapshot


def _funnel(n_leaves: int) -> Snapshot:
    nodes = {
        "DA": Node("DA", "DOMAIN ADMINS", "Group", 0, domain="CORP"),
        "HELP": Node("HELP", "HELPDESK", "Group", 1, domain="CORP"),
    }
    edges = [Edge("HELP", "HELPDESK", "Group", "GenericAll", "DA", "CORP")]
    for i in range(n_leaves):
        nodes[f"u{i}"] = Node(f"u{i}", f"U{i}", "User", 2, domain="CORP")
        edges.append(Edge(f"u{i}", f"U{i}", "User", "MemberOf", "HELP", "CORP"))
    return Snapshot({"DA"}, nodes, edges, rounds=2)


def test_mermaid_bundles_large_leaf_fanin() -> None:
    out = to_mermaid(_funnel(10), {"HELP"}, bundle_threshold=8)
    assert out.startswith("graph RL")
    assert "DOMAIN ADMINS" in out and "HELPDESK" in out
    assert "(10 principals)" in out          # the 10 leaves collapsed
    assert '"U0"' not in out                  # individual leaves hidden
    assert "class" in out and "choke" in out  # HELP styled as a choke point


def test_mermaid_no_bundle_below_threshold() -> None:
    out = to_mermaid(_funnel(10), {"HELP"}, bundle_threshold=20)
    assert "(10 principals)" not in out
    assert '"U0"' in out                       # leaves shown individually


def test_dot_render() -> None:
    out = to_dot(_funnel(10), {"HELP"}, bundle_threshold=8)
    assert out.startswith("digraph")
    assert "rankdir=RL" in out
    assert "(10 principals)" in out
    assert "}" in out

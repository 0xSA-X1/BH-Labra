"""Tests for graph/literal parsing and path reconstruction."""

from __future__ import annotations

from bhe.parsing.graph import (
    extract_literals,
    nodes_table,
    parse_graph,
    reconstruct_paths,
)

GRAPH = {
    "data": {
        "nodes": {
            "1": {"objectId": "S-1", "kind": "User",
                  "properties": {"name": "ALICE@CORP", "objectid": "S-1"}},
            "2": {"objectId": "S-2", "kind": "Group",
                  "properties": {"name": "HELPDESK@CORP", "objectid": "S-2"}},
            "3": {"objectId": "S-3", "kind": "Group",
                  "properties": {"name": "DOMAIN ADMINS@CORP", "objectid": "S-3"}},
        },
        "edges": [
            {"source": "1", "target": "2", "label": "MemberOf", "kind": "MemberOf"},
            {"source": "2", "target": "3", "label": "GenericAll", "kind": "GenericAll"},
        ],
    }
}


def test_parse_graph_counts() -> None:
    nodes, edges = parse_graph(GRAPH)
    assert len(nodes) == 3
    assert len(edges) == 2
    assert {n.object_id for n in nodes} == {"S-1", "S-2", "S-3"}


def test_nodes_table_rows() -> None:
    rows = nodes_table(GRAPH)
    assert rows[0]["name"] == "ALICE@CORP"
    assert rows[0]["kind"] == "User"


def test_reconstruct_single_path() -> None:
    nodes, edges = parse_graph(GRAPH)
    paths = reconstruct_paths(nodes, edges)
    assert len(paths) == 1
    path = paths[0]
    assert path.length() == 2
    assert path.nodes[0].object_id == "S-1"
    assert path.nodes[-1].object_id == "S-3"


def test_reconstruct_handles_cycle() -> None:
    nodes, edges = parse_graph(
        {
            "data": {
                "nodes": {
                    "1": {"objectId": "A", "properties": {}},
                    "2": {"objectId": "B", "properties": {}},
                },
                "edges": [
                    {"source": "1", "target": "2"},
                    {"source": "2", "target": "1"},
                ],
            }
        }
    )
    # Should terminate (no infinite recursion) and return at least one chain.
    paths = reconstruct_paths(nodes, edges)
    assert paths  # does not hang or crash


def test_extract_literals_list() -> None:
    resp = {"data": [{"sid": "S-1"}, {"sid": "S-2"}]}
    assert extract_literals(resp) == [{"sid": "S-1"}, {"sid": "S-2"}]


def test_extract_literals_ignores_graph() -> None:
    assert extract_literals(GRAPH) == []

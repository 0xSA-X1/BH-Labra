"""Tests for the read-only enforcement layer."""

from __future__ import annotations

import pytest

from bhe.api.readonly import (
    ReadOnlyViolation,
    assert_cypher_readonly,
    assert_request_allowed,
)


# --- transport allowlist ----------------------------------------------------

def test_get_is_allowed() -> None:
    assert_request_allowed("GET", "/api/v2/available-domains")
    assert_request_allowed("get", "/api/v2/clients/123")  # case-insensitive


def test_cypher_post_is_allowed() -> None:
    assert_request_allowed("POST", "/api/v2/graphs/cypher")
    assert_request_allowed("POST", "/api/v2/graphs/cypher?foo=bar")  # query ignored


@pytest.mark.parametrize(
    "method,endpoint",
    [
        ("PUT", "/api/v2/attack-paths/1/acceptance"),
        ("DELETE", "/api/v2/clients/123"),
        ("PATCH", "/api/v2/self"),
        ("POST", "/api/v2/clients"),  # POST to a non-cypher path
        ("POST", "/api/v2/analysis"),
    ],
)
def test_mutating_requests_are_blocked(method: str, endpoint: str) -> None:
    with pytest.raises(ReadOnlyViolation):
        assert_request_allowed(method, endpoint)


# --- cypher guard -----------------------------------------------------------

@pytest.mark.parametrize(
    "query",
    [
        "MATCH (u:User) RETURN u LIMIT 25",
        "MATCH p=shortestPath((a)-[*1..]->(b)) RETURN p",
        "MATCH (n) WHERE n.name = 'RESET' RETURN n",      # 'RESET' contains 'SET'
        "MATCH (n) WHERE n.created > 0 RETURN n.created",  # 'created' contains 'create'
        "MATCH (g:Group) RETURN g.name AS asset",          # 'asset' is fine
    ],
)
def test_read_queries_pass(query: str) -> None:
    assert_cypher_readonly(query)  # should not raise


@pytest.mark.parametrize(
    "query",
    [
        "CREATE (n:User {name:'x'})",
        "MATCH (n) SET n.owned = true",
        "MATCH (n) DETACH DELETE n",
        "MATCH (n) REMOVE n.tag",
        "MERGE (n:User {name:'x'})",
        "MATCH (n) DELETE n",
        "FOREACH (x IN [1] | SET x.a = 1)",
        "CALL apoc.create.node(['X'], {})",
        "// comment\nCREATE (n)",  # comment-hidden write still blocked
    ],
)
def test_write_queries_are_blocked(query: str) -> None:
    with pytest.raises(ReadOnlyViolation):
        assert_cypher_readonly(query)

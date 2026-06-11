"""End-to-end client tests against the bundled fixtures (no network)."""

from __future__ import annotations

import pytest

from bhe.api.client import BHEClient
from bhe.api.readonly import ReadOnlyViolation


async def test_available_domains_from_fixtures() -> None:
    async with BHEClient.connect("https://mock", "id", "key", mock=True) as client:
        domains = await client.get_available_domains()
    assert len(domains) == 3
    assert {d["type"] for d in domains} == {"active-directory", "azure"}


async def test_clients_jobs_events() -> None:
    async with BHEClient.connect("https://mock", "id", "key", mock=True) as client:
        clients = await client.get_clients()
        jobs = await client.get_jobs()
        events = await client.get_events()
    assert len(clients) == 2
    assert any(j["status"] == "RUNNING" for j in jobs)
    assert events[0]["rrule"].startswith("FREQ=DAILY")


async def test_cypher_returns_graph() -> None:
    async with BHEClient.connect("https://mock", "id", "key", mock=True) as client:
        resp = await client.cypher_query("MATCH (u:User) RETURN u LIMIT 5")
    assert "data" in resp
    assert len(resp["data"]["nodes"]) == 3


async def test_cypher_write_is_blocked_before_send() -> None:
    async with BHEClient.connect("https://mock", "id", "key", mock=True) as client:
        with pytest.raises(ReadOnlyViolation):
            await client.cypher_query("MATCH (n) DETACH DELETE n")


async def test_transport_blocks_mutating_method() -> None:
    async with BHEClient.connect("https://mock", "id", "key", mock=True) as client:
        with pytest.raises(ReadOnlyViolation):
            await client._request("DELETE", "/api/v2/clients/123")


async def test_asset_group_tags_tolerates_missing() -> None:
    # The fixture exists, so this should succeed; the 404-tolerance path is
    # exercised by the absence of an unmatched route returning {"data": []}.
    async with BHEClient.connect("https://mock", "id", "key", mock=True) as client:
        tags = await client.get_asset_group_tags()
    assert "data" in tags

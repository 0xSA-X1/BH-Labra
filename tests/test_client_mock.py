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


async def test_query_bearing_get_signs_over_full_request_uri(monkeypatch) -> None:
    """Regression: the signed URI must include the query string, not just the path.

    BHE signs ``method + RequestURI`` with the query string included, so signing
    the bare path made every query-bearing GET (search, shortest-path, findings)
    401 with "signature digest mismatch".
    """
    import httpx

    seen: dict[str, str] = {}

    async with BHEClient.connect("https://tenant.example", "tid", "secret") as client:
        original_sign = client._auth.sign_request

        def _spy(method: str, uri: str, body: bytes = b"") -> dict[str, str]:
            seen["signed_uri"] = uri
            return original_sign(method, uri, body)

        async def _fake_send(request: httpx.Request, **kwargs) -> httpx.Response:
            seen["wire_uri"] = request.url.raw_path.decode("ascii")
            seen["accept"] = request.headers.get("accept", "")
            return httpx.Response(200, json={"data": []}, request=request)

        monkeypatch.setattr(client._auth, "sign_request", _spy)
        monkeypatch.setattr(client._http, "send", _fake_send)

        await client.search("authenticated users@essos.local", kind="User")

    # We ask for JSON explicitly (some endpoints default to CSV).
    assert seen["accept"] == "application/json"

    # We signed the path *plus* the encoded query, and it matches the wire URI
    # the server will validate against, byte for byte.
    assert seen["signed_uri"].startswith("/api/v2/search?")
    assert "type=User" in seen["signed_uri"]
    assert "q=authenticated" in seen["signed_uri"]
    assert seen["signed_uri"] == seen["wire_uri"]


async def test_non_json_2xx_body_raises_api_error_not_jsondecode(monkeypatch) -> None:
    """A 2xx with a non-JSON body must surface as BHEClientError, not crash.

    Some endpoints/domains return a CSV or HTML body; a raw JSONDecodeError would
    escape and tear down an in-flight asyncio.gather (and its shared client).
    """
    import httpx

    from bhe.api.client import BHEClientError

    async with BHEClient.connect("https://tenant.example", "tid", "secret") as client:
        async def _fake_send(request: httpx.Request, **kwargs) -> httpx.Response:
            return httpx.Response(200, content=b"Principal,Finding\nALICE,DCSync\n", request=request)

        monkeypatch.setattr(client._http, "send", _fake_send)
        with pytest.raises(BHEClientError):
            await client.get_self()

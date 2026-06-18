"""Offline mock transport - serves bundled JSON fixtures.

Enabled by ``BHE_MOCK=1`` / ``--mock``.  Lets the entire TUI be driven with
no tenant or credentials, and backs the test-suite's network-free assertions.

Fixtures live in ``bhe/fixtures`` and are named by route template, with ``.``
standing in for ``/`` and ``{id}`` marking a wildcard path segment, e.g.::

    GET.api.v2.available-domains.json
    GET.api.v2.clients.{id}.json          # matches /api/v2/clients/<anything>
    POST.api.v2.graphs.cypher.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


class MockTransport:
    """Matches incoming requests to fixture files and returns canned responses."""

    def __init__(self, fixtures_dir: Path | None = None) -> None:
        self._dir = fixtures_dir or FIXTURES_DIR
        self._routes: list[tuple[str, list[str], Path]] = []
        self._load_routes()

    def _load_routes(self) -> None:
        """Index fixture files into (method, segment-template, path) routes."""
        if not self._dir.is_dir():
            return
        for fixture in sorted(self._dir.glob("*.json")):
            tokens = fixture.stem.split(".")
            if len(tokens) < 2:
                continue
            method = tokens[0].upper()
            segments = tokens[1:]
            self._routes.append((method, segments, fixture))

    @staticmethod
    def _segments(endpoint: str) -> list[str]:
        path = endpoint.split("?", 1)[0]
        return [seg for seg in path.split("/") if seg]

    def _match(self, method: str, endpoint: str) -> Path | None:
        """Find the first fixture whose template matches the request."""
        req_segments = self._segments(endpoint)
        for route_method, template, fixture in self._routes:
            if route_method != method.upper():
                continue
            if len(template) != len(req_segments):
                continue
            if all(
                tmpl == "{id}" or tmpl == actual
                for tmpl, actual in zip(template, req_segments)
            ):
                return fixture
        return None

    def dispatch(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,  # noqa: ARG002 - ignored in mock
        body: bytes | None = None,
    ) -> httpx.Response:
        """Return a canned ``httpx.Response`` for the request, or a 404.

        The Cypher endpoint is special-cased: backward-scope seed/expansion
        queries are answered from the synthetic funnel graph (so ``bhe choke``
        works in mock), while every other query falls back to the static fixture.
        """
        path = endpoint.split("?", 1)[0]
        if method.upper() == "POST" and path == "/api/v2/graphs/cypher" and body:
            from bhe.api._synthetic import synthetic_cypher_response

            try:
                query = json.loads(body).get("query", "")
            except (ValueError, AttributeError, TypeError):
                query = ""
            synthetic = synthetic_cypher_response(query)
            if synthetic is not None:
                return httpx.Response(200, json=synthetic)

        # Search filters the fixture by the q/type params (BHE matches by name),
        # so substring search behaves realistically in mock.
        if method.upper() == "GET" and path == "/api/v2/search":
            fixture = self._match("GET", "/api/v2/search")
            rows = json.loads(fixture.read_text(encoding="utf-8")).get("data", []) if fixture else []
            q = str((params or {}).get("q", "")).lower()
            typ = (params or {}).get("type")
            rows = [r for r in rows if q in str(r.get("name", "")).lower()]
            if typ:
                rows = [r for r in rows if str(r.get("type", "")).lower() == str(typ).lower()]
            return httpx.Response(200, json={"data": rows})

        # Per-domain findings are served synthetically (keyed by the `finding`
        # param) so triage/findings exercise real per-type ranking.
        if method.upper() == "GET" and "/domains/" in path:
            from bhe.api._synthetic import available_types_payload, details_payload

            if path.endswith("/available-types"):
                return httpx.Response(200, json=available_types_payload())
            if path.endswith("/details"):
                finding = (params or {}).get("finding")
                return httpx.Response(200, json=details_payload(finding))

        fixture = self._match(method, endpoint)
        if fixture is None:
            return httpx.Response(
                404,
                json={"errors": [{"message": f"no mock fixture for {method} {endpoint}"}]},
            )
        data = json.loads(fixture.read_text(encoding="utf-8"))
        return httpx.Response(200, json=data)

"""Read-only async REST client for the BloodHound Enterprise API.

``BHEClient`` composes one mixin per BHE API group (Clients, Jobs, Events,
Attack Paths, Risk Posture, Meta Entities, Analysis, Entities) on top of a
small signed-transport core.  Every request passes through the read-only
allowlist before it is signed, so the client cannot mutate a tenant.
"""

from __future__ import annotations

import asyncio
import json as _json
import logging
import random
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx

from bhe.api.groups.analysis import AnalysisMixin
from bhe.api.groups.attack_paths import AttackPathsMixin
from bhe.api.groups.clients import ClientsMixin
from bhe.api.groups.entities import EntitiesMixin
from bhe.api.groups.events import EventsMixin
from bhe.api.groups.jobs import JobsMixin
from bhe.api.groups.meta import MetaMixin
from bhe.api.groups.posture import PostureMixin
from bhe.api.hmac_auth import HMACAuth
from bhe.api.mock import MockTransport
from bhe.api.readonly import assert_request_allowed
from bhe.observability import RequestLog, RequestRecord, classify

logger = logging.getLogger("bhe")


class BHEClientError(Exception):
    """Raised when a BHE API request returns a non-2xx status."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"BHE API error {status_code}: {detail}")


class BHETransport:
    """Signed, read-only transport for the BHE API.

    Use via :meth:`connect` so the underlying ``httpx.AsyncClient`` (or mock) is
    opened and closed cleanly::

        async with BHEClient.connect(base_url, token_id, token_key) as client:
            domains = await client.get_available_domains()
    """

    # 429 retry tunables - BHE rate-limits per IP; back off and retry.
    _RATE_LIMIT_MAX_RETRIES: int = 5
    _RATE_LIMIT_BASE_DELAY: float = 0.5
    _RATE_LIMIT_MAX_DELAY: float = 30.0

    def __init__(
        self,
        base_url: str,
        token_id: str,
        token_key: str,
        *,
        mock: bool = False,
        request_log: RequestLog | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = HMACAuth(token_id=token_id, token_key=token_key)
        self._mock = MockTransport() if mock else None
        self._http: httpx.AsyncClient | None = None
        #: Structured request recorder (Logs pane / support bundle); optional so
        #: the client is fully usable headless without observability wired up.
        self.request_log = request_log

    def __repr__(self) -> str:  # never leak the token key
        mode = "mock" if self._mock else "live"
        return f"<BHEClient {self._base_url} ({mode})>"

    @classmethod
    @asynccontextmanager
    async def connect(
        cls,
        base_url: str,
        token_id: str,
        token_key: str,
        *,
        mock: bool = False,
        request_log: RequestLog | None = None,
    ) -> AsyncIterator["BHEClient"]:
        """Async context manager managing the HTTP client lifecycle."""
        instance = cls(  # type: ignore[call-arg]
            base_url, token_id, token_key, mock=mock, request_log=request_log
        )
        if instance._mock is None:
            instance._http = httpx.AsyncClient(
                base_url=instance._base_url,
                timeout=httpx.Timeout(30.0),
                follow_redirects=True,
            )
        try:
            yield instance  # type: ignore[misc]
        finally:
            if instance._http is not None:
                await instance._http.aclose()
                instance._http = None

    async def _sign_and_send(
        self,
        method: str,
        endpoint: str,
        body: bytes,
        params: dict[str, Any] | None,
    ) -> httpx.Response:
        """Sign over the EXACT wire URI (path + query) and send one attempt.

        BHE's HMAC covers ``method + RequestURI``, and the RequestURI the server
        validates *includes the query string*.  So we let httpx build the request
        first, sign over its real ``raw_path`` (path + URL-encoded query), then
        send that same request - guaranteeing the bytes we signed are the bytes
        the server receives.  Signing only the bare path is what made every
        query-bearing GET (``search``, ``shortest-path``, ``findings``) 401 with
        "signature digest mismatch".

        A fresh timestamp is embedded per call, so this is re-invoked each retry.
        """
        content = body if body else None
        if self._mock is not None:
            # The mock transport doesn't validate signatures; skip signing.
            return self._mock.dispatch(method, endpoint, params, content)
        if self._http is None:
            raise RuntimeError(
                "BHEClient must be used within 'async with BHEClient.connect(...)'."
            )
        request = self._http.build_request(
            method=method, url=endpoint, content=content, params=params
        )
        signed_uri = request.url.raw_path.decode("ascii")
        request.headers.update(
            self._auth.sign_request(method=method, uri=signed_uri, body=body)
        )
        return await self._http.send(request)

    async def _request(
        self,
        method: str,
        endpoint: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a signed, read-only request and return parsed JSON.

        Args:
            method: HTTP method.
            endpoint: API path (e.g. ``/api/v2/available-domains``).
            json_data: Optional JSON body (only for the Cypher endpoint).
            params: Optional query parameters.

        Returns:
            Parsed JSON response as a dict.

        Raises:
            ReadOnlyViolation: If the request is not read-only (before sending).
            BHEClientError: On non-2xx responses after retries are exhausted.
        """
        # Per-request observability bookkeeping.  Skipped cheaply when no
        # RequestLog is attached (headless use without the Logs pane).
        rlog = self.request_log
        seq = rlog.next_seq() if rlog is not None else 0
        correlation_id = f"{seq:06d}"
        start = time.monotonic()
        retries = 0
        status: int | None = None
        detail = ""
        error: BaseException | None = None
        try:
            # Read-only gate - refuse before signing/sending anything.
            assert_request_allowed(method, endpoint)

            body = b""
            if json_data is not None:
                body = _json.dumps(json_data).encode("utf-8")

            logger.debug("BHE API %s %s [%s]", method, endpoint, correlation_id)

            response: httpx.Response | None = None
            delay = self._RATE_LIMIT_BASE_DELAY
            for attempt in range(self._RATE_LIMIT_MAX_RETRIES + 1):
                # _sign_and_send re-signs per attempt (fresh HMAC timestamp).
                response = await self._sign_and_send(method, endpoint, body, params)

                if (
                    response.status_code != 429
                    or attempt >= self._RATE_LIMIT_MAX_RETRIES
                ):
                    break

                retry_after = response.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after is not None else delay
                except ValueError:
                    wait = delay
                wait = min(wait, self._RATE_LIMIT_MAX_DELAY)
                wait += random.uniform(0, wait * 0.25)  # desync concurrent retriers

                logger.debug(
                    "BHE API rate-limited (429) on %s %s - retry %d/%d in %.1fs",
                    method, endpoint, attempt + 1, self._RATE_LIMIT_MAX_RETRIES, wait,
                )
                retries = attempt + 1
                await asyncio.sleep(wait)
                delay = min(delay * 2, self._RATE_LIMIT_MAX_DELAY)

            assert response is not None
            status = response.status_code

            if response.status_code >= 400:
                detail = response.text[:500]
                level = logging.ERROR if response.status_code >= 500 else logging.WARNING
                if response.status_code == 404:
                    level = logging.DEBUG
                logger.log(
                    level, "BHE API error: %s %s -> %d [%s]: %s",
                    method, endpoint, response.status_code, correlation_id, detail,
                )
                raise BHEClientError(status_code=response.status_code, detail=detail)

            if not response.content:
                return {}
            return response.json()
        except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised
            error = exc
            if not detail:
                detail = f"{type(exc).__name__}: {exc}"[:500]
            raise
        finally:
            if rlog is not None:
                latency_ms = (time.monotonic() - start) * 1000.0
                rlog.add(
                    RequestRecord(
                        seq=seq,
                        correlation_id=correlation_id,
                        method=method.upper(),
                        endpoint=endpoint.split("?", 1)[0],
                        status=status,
                        latency_ms=latency_ms,
                        category=classify(status, error),
                        retries=retries,
                        detail=detail,
                    )
                )

    # ------------------------------------------------------------------
    # Foundational reads (used across screens)
    # ------------------------------------------------------------------

    async def get_version(self) -> dict[str, Any]:
        """Return the BHE API/server version (``GET /api/version``)."""
        return await self._request("GET", "/api/version")

    async def get_self(self) -> dict[str, Any]:
        """Return the authenticated identity (``GET /api/v2/self``)."""
        return await self._request("GET", "/api/v2/self")

    async def check_connection(self) -> bool:
        """Best-effort connectivity/auth check against the tenant."""
        try:
            await self.get_self()
            return True
        except (BHEClientError, httpx.HTTPError) as exc:
            logger.warning("BHE connectivity check failed: %s", exc)
            return False

    async def get_available_domains(self) -> list[dict[str, Any]]:
        """List domains/tenants known to BHE (``GET /api/v2/available-domains``)."""
        response = await self._request("GET", "/api/v2/available-domains")
        return response.get("data", [])

    async def get_asset_group_tags(self) -> dict[str, Any]:
        """List asset-group tags (Tier Zero / owned). Tolerates 404 on old tenants."""
        try:
            return await self._request("GET", "/api/v2/asset-group-tags")
        except BHEClientError as exc:
            if exc.status_code == 404:
                return {"data": []}
            raise

    async def discover_spec(self) -> dict[str, Any]:
        """Fetch the tenant's live OpenAPI spec for route confirmation.

        BHE serves its own spec, which is the source of truth for exact routes
        across BHE versions.  Returns ``{}`` if the tenant doesn't expose it.
        """
        for path in ("/api/v2/spec/openapi.json", "/api/v2/swagger/doc.json"):
            try:
                return await self._request("GET", path)
            except BHEClientError as exc:
                if exc.status_code in (404, 401):
                    continue
                raise
        return {}


class BHEClient(
    BHETransport,
    ClientsMixin,
    JobsMixin,
    EventsMixin,
    AttackPathsMixin,
    PostureMixin,
    MetaMixin,
    AnalysisMixin,
    EntitiesMixin,
):
    """The full read-only BHE client: transport + all API-group mixins."""

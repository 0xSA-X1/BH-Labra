"""Clients API group - registered collection clients (SharpHound/AzureHound Enterprise).

Read-only: listing and inspecting clients only.  Token rotation, creation,
updates and deletion are intentionally not exposed.
"""

from __future__ import annotations

from typing import Any

from bhe.api.groups._base import GroupMixin


class ClientsMixin(GroupMixin):
    """Read access to the Clients group."""

    async def get_clients(self) -> list[dict[str, Any]]:
        """List all registered collection clients (``GET /api/v2/clients``)."""
        return self._data(await self._request("GET", "/api/v2/clients")) or []

    async def get_client(self, client_id: str) -> dict[str, Any]:
        """Get a single client's detail (``GET /api/v2/clients/{id}``)."""
        return self._data(
            await self._request("GET", f"/api/v2/clients/{client_id}")
        )

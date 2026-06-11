"""Attack Paths API group - findings, details, trends and per-domain sparklines.

Read-only: risk acceptance (``PUT .../acceptance``) and analysis re-runs are not
exposed.
"""

from __future__ import annotations

from typing import Any

from bhe.api.groups._base import GroupMixin


class AttackPathsMixin(GroupMixin):
    """Read access to the Attack Paths group."""

    async def get_attack_paths(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """List attack paths (``GET /api/v2/attack-paths``)."""
        return await self._request("GET", "/api/v2/attack-paths", params=params)

    async def get_attack_paths_details(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Get attack-path detail rows (``GET /api/v2/attack-paths/details``)."""
        return await self._request(
            "GET", "/api/v2/attack-paths/details", params=params
        )

    async def get_finding_trends(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Get finding trend data (``GET /api/v2/attack-paths/finding-trends``)."""
        return await self._request(
            "GET", "/api/v2/attack-paths/finding-trends", params=params
        )

    async def get_attack_path_types(self) -> list[dict[str, Any]]:
        """List recognized attack-path types (``GET /api/v2/attack-path-types``)."""
        return self._data(
            await self._request("GET", "/api/v2/attack-path-types")
        ) or []

    async def get_domain_attack_path_findings(
        self, domain_id: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Get a domain's findings (``GET /api/v2/domains/{id}/attack-path-findings``)."""
        return await self._request(
            "GET", f"/api/v2/domains/{domain_id}/attack-path-findings", params=params
        )

    async def get_domain_details(self, domain_id: str) -> dict[str, Any]:
        """Get a domain's attack-path detail (``GET /api/v2/domains/{id}/details``)."""
        return await self._request("GET", f"/api/v2/domains/{domain_id}/details")

    async def get_domain_sparkline(
        self, domain_id: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Get a domain's exposure sparkline (``GET /api/v2/domains/{id}/sparkline``)."""
        return await self._request(
            "GET", f"/api/v2/domains/{domain_id}/sparkline", params=params
        )

    async def get_domain_available_types(self, domain_id: str) -> list[dict[str, Any]]:
        """List a domain's available finding types (``.../available-types``)."""
        return self._data(
            await self._request(
                "GET", f"/api/v2/domains/{domain_id}/available-types"
            )
        ) or []

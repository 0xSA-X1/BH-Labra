"""Data Quality API group - collection completeness & per-domain quality stats.

Read-only.  These back ``bhe quality`` so you can confirm collection is healthy
*before* trusting findings (e.g. if sessions/local-groups weren't collected, the
attack-path analysis is blind to those edges).
"""

from __future__ import annotations

from typing import Any

from bhe.api.groups._base import GroupMixin


class DataQualityMixin(GroupMixin):
    """Read access to the Data Quality group."""

    async def get_completeness(self) -> dict[str, Any]:
        """Database completeness (% of local admins & sessions collected).

        ``GET /api/v2/completeness`` -> ``{"data": {<metric>: <0-1 or %>}}``.
        """
        return await self._request("GET", "/api/v2/completeness")

    async def get_ad_domain_quality(
        self, domain_id: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """A domain's data-quality stats over time.

        ``GET /api/v2/ad-domains/{domain_id}/data-quality-stats`` -> counts per
        object type plus ``session_completeness`` / ``local_group_completeness``.
        """
        return await self._request(
            "GET", f"/api/v2/ad-domains/{domain_id}/data-quality-stats", params=params
        )

    async def get_azure_tenant_quality(
        self, tenant_id: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """An Azure tenant's data-quality stats (``.../azure-tenants/{id}/data-quality-stats``)."""
        return await self._request(
            "GET", f"/api/v2/azure-tenants/{tenant_id}/data-quality-stats", params=params
        )

    async def get_platform_quality(
        self, platform_id: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Estate-wide quality for a platform (``platform_id`` is ``ad`` or ``azure``).

        ``GET /api/v2/platform/{platform_id}/data-quality-stats``.
        """
        return await self._request(
            "GET", f"/api/v2/platform/{platform_id}/data-quality-stats", params=params
        )

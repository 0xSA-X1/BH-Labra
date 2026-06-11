"""Risk Posture API group - posture stats and historical trends."""

from __future__ import annotations

from typing import Any

from bhe.api.groups._base import GroupMixin


class PostureMixin(GroupMixin):
    """Read access to the Risk Posture group."""

    async def get_posture_stats(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Get current posture stats (``GET /api/v2/posture-stats``).

        Common params: ``from``/``to`` (RFC3339 range), ``domain_sid``, ``sort_by``.
        """
        return await self._request("GET", "/api/v2/posture-stats", params=params)

    async def get_posture_history(
        self, data_type: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Get a posture history series (``GET /api/v2/posture-history/{data_type}``).

        ``data_type`` is e.g. ``findings``, ``exposure``, ``assets`` depending on
        the tenant/version.
        """
        return await self._request(
            "GET", f"/api/v2/posture-history/{data_type}", params=params
        )

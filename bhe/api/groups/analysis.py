"""Analysis API group - precomputed analysis artifacts (read-only).

Only the GET artifact endpoints are exposed; triggering an analysis run (a POST)
is intentionally omitted.
"""

from __future__ import annotations

from typing import Any

from bhe.api.groups._base import GroupMixin


class AnalysisMixin(GroupMixin):
    """Read access to the Analysis group."""

    async def get_asset_group_combo_node(self, asset_group_id: str) -> dict[str, Any]:
        """Get an asset group's combo node (``GET /api/v2/asset-groups/{id}/combo-node``)."""
        return await self._request(
            "GET", f"/api/v2/asset-groups/{asset_group_id}/combo-node"
        )

    async def get_meta_node(self, object_id: str) -> dict[str, Any]:
        """Get an analysis meta node (``GET /api/v2/meta-nodes/{id}``)."""
        return await self._request("GET", f"/api/v2/meta-nodes/{object_id}")

    async def get_meta_tree_analysis(self, object_id: str) -> dict[str, Any]:
        """Get an analysis meta tree (``GET /api/v2/meta-trees/{id}``)."""
        return await self._request("GET", f"/api/v2/meta-trees/{object_id}")

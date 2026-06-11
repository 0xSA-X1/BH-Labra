"""Meta Entities API group - aggregate/meta node lookups."""

from __future__ import annotations

from typing import Any

from bhe.api.groups._base import GroupMixin


class MetaMixin(GroupMixin):
    """Read access to the Meta Entities group."""

    async def get_meta_entity(self, object_id: str) -> dict[str, Any]:
        """Get a meta entity by object id (``GET /api/v2/meta/{object_id}``)."""
        return await self._request("GET", f"/api/v2/meta/{object_id}")

    async def get_meta_tree(self, domain_id: str) -> dict[str, Any]:
        """Get a domain's meta tree (``GET /api/v2/meta-trees/{domain_id}``)."""
        return await self._request("GET", f"/api/v2/meta-trees/{domain_id}")

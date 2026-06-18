"""Entities, search, pathfinding and the (guarded) Cypher endpoint.

These back the Search/enrich and Cypher screens.  The Cypher endpoint is the
only POST bhe issues, and every query is run through the read-only Cypher
guard before it is sent.
"""

from __future__ import annotations

from typing import Any

from bhe.api.groups._base import GroupMixin
from bhe.api.readonly import assert_cypher_readonly


class EntitiesMixin(GroupMixin):
    """Read access to search, entity detail and pathfinding."""

    # ------------------------------------------------------------------
    # Cypher (guarded read-only)
    # ------------------------------------------------------------------

    async def cypher_query(
        self,
        query: str,
        include_properties: bool = True,
    ) -> dict[str, Any]:
        """Run a read-only Cypher query (``POST /api/v2/graphs/cypher``).

        Args:
            query: Cypher text.  Rejected before sending if it contains a write
                clause (CREATE/MERGE/SET/DELETE/...).
            include_properties: Ask BHE to include node/edge properties.

        Returns:
            Raw graph JSON (``{"data": {"nodes": {...}, "edges": [...]}}``) or a
            ``literals`` payload for scalar-returning queries.

        Raises:
            ReadOnlyViolation: If the query is not read-only.
        """
        from bhe.api.client import BHEClientError

        assert_cypher_readonly(query)
        payload: dict[str, Any] = {
            "query": query,
            "include_properties": include_properties,
        }
        try:
            return await self._request("POST", "/api/v2/graphs/cypher", json_data=payload)
        except BHEClientError as exc:
            if exc.status_code == 404:
                # BHE answers 404 when a Cypher query yields no graph (a
                # shortestPath with no path, or a MATCH that hits nothing).
                # That's an empty result, not an error - return an empty graph so
                # `hunt`, `cypher`, and the backward-scope engine all degrade
                # gracefully instead of surfacing a scary "resource not found".
                return {"data": {"nodes": {}, "edges": []}}
            raise

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self, term: str, kind: str | None = None
    ) -> list[dict[str, Any]]:
        """Search for nodes by name/objectid (``GET /api/v2/search``).

        Args:
            term: Query string (name fragment or objectid).
            kind: Optional node kind filter (e.g. ``User``, ``Computer``).
        """
        params: dict[str, Any] = {"q": term}
        if kind:
            params["type"] = kind
        return self._data(
            await self._request("GET", "/api/v2/search", params=params)
        ) or []

    # ------------------------------------------------------------------
    # Entity detail
    # ------------------------------------------------------------------

    async def get_user(self, object_id: str) -> dict[str, Any]:
        """Get user entity detail."""
        return await self._request("GET", f"/api/v2/users/{object_id}")

    async def get_computer(self, object_id: str) -> dict[str, Any]:
        """Get computer entity detail."""
        return await self._request("GET", f"/api/v2/computers/{object_id}")

    async def get_group(self, object_id: str) -> dict[str, Any]:
        """Get group entity detail."""
        return await self._request("GET", f"/api/v2/groups/{object_id}")

    async def get_domain(self, object_id: str) -> dict[str, Any]:
        """Get domain entity detail."""
        return await self._request("GET", f"/api/v2/domains/{object_id}")

    async def get_gpo(self, object_id: str) -> dict[str, Any]:
        """Get GPO entity detail."""
        return await self._request("GET", f"/api/v2/gpos/{object_id}")

    async def get_entity_relationship(
        self, plural: str, object_id: str, slug: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Generic entity-relationship read (``GET /api/v2/{plural}/{id}/{slug}``).

        Backs ``bhe entity --show`` for sessions / members / admin-rights /
        controllers / etc.  ``plural`` is the kind's collection (users/computers/
        groups/...), ``slug`` the relationship path segment.
        """
        return await self._request(
            "GET", f"/api/v2/{plural}/{object_id}/{slug}", params=params
        )

    async def get_user_memberships(self, object_id: str) -> dict[str, Any]:
        """Get a user's group memberships."""
        return await self._request("GET", f"/api/v2/users/{object_id}/memberships")

    async def get_user_admin_rights(self, object_id: str) -> dict[str, Any]:
        """Get computers where a user has admin rights."""
        return await self._request("GET", f"/api/v2/users/{object_id}/admin-rights")

    async def get_user_sessions(self, object_id: str) -> dict[str, Any]:
        """Get a user's sessions."""
        return await self._request("GET", f"/api/v2/users/{object_id}/sessions")

    async def get_computer_sessions(self, object_id: str) -> dict[str, Any]:
        """Get sessions observed on a computer."""
        return await self._request("GET", f"/api/v2/computers/{object_id}/sessions")

    async def get_computer_admins(self, object_id: str) -> dict[str, Any]:
        """Get a computer's local admins."""
        return await self._request("GET", f"/api/v2/computers/{object_id}/admin-users")

    async def get_group_members(self, object_id: str) -> dict[str, Any]:
        """Get a group's members."""
        return await self._request("GET", f"/api/v2/groups/{object_id}/members")

    async def get_domain_controllers(self, object_id: str) -> dict[str, Any]:
        """Get a domain's controllers."""
        return await self._request("GET", f"/api/v2/domains/{object_id}/controllers")

    # ------------------------------------------------------------------
    # Pathfinding
    # ------------------------------------------------------------------

    async def get_shortest_path(
        self, start_node: str, end_node: str
    ) -> dict[str, Any]:
        """Shortest path between two nodes (``GET /api/v2/graphs/shortest-path``)."""
        return await self._request(
            "GET",
            "/api/v2/graphs/shortest-path",
            params={"start_node": start_node, "end_node": end_node},
        )

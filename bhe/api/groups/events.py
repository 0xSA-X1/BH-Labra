"""Events API group - scheduled collection events (a.k.a. Schedules).

Read-only: listing/inspecting schedules.  Creating, editing and deleting
schedules are not exposed.
"""

from __future__ import annotations

from typing import Any

from bhe.api.groups._base import GroupMixin


class EventsMixin(GroupMixin):
    """Read access to the Events (Schedules) group."""

    async def get_events(self) -> list[dict[str, Any]]:
        """List all scheduled collection events (``GET /api/v2/events``)."""
        return self._data(await self._request("GET", "/api/v2/events")) or []

    async def get_event(self, event_id: str | int) -> dict[str, Any]:
        """Get a single schedule's detail (``GET /api/v2/events/{id}``)."""
        return self._data(await self._request("GET", f"/api/v2/events/{event_id}"))

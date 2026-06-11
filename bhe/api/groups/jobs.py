"""Jobs API group - collection job runs.

Read-only: listing/inspecting jobs.  Starting and cancelling jobs are not
exposed (they would mutate tenant state).
"""

from __future__ import annotations

from typing import Any

from bhe.api.groups._base import GroupMixin


class JobsMixin(GroupMixin):
    """Read access to the Jobs group."""

    async def get_jobs(
        self, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """List all collection jobs (``GET /api/v2/jobs``)."""
        return self._data(await self._request("GET", "/api/v2/jobs", params=params)) or []

    async def get_job(self, job_id: str | int) -> dict[str, Any]:
        """Get a single job's detail (``GET /api/v2/jobs/{id}``)."""
        return self._data(await self._request("GET", f"/api/v2/jobs/{job_id}"))

    async def get_current_jobs(self) -> list[dict[str, Any]]:
        """List currently-running jobs (``GET /api/v2/jobs/current``)."""
        return self._data(await self._request("GET", "/api/v2/jobs/current")) or []

    async def get_finished_jobs(self) -> list[dict[str, Any]]:
        """List finished jobs (``GET /api/v2/jobs/finished``)."""
        return self._data(await self._request("GET", "/api/v2/jobs/finished")) or []

    async def get_client_jobs(self, client_id: str) -> list[dict[str, Any]]:
        """List a client's jobs (``GET /api/v2/clients/{id}/jobs``)."""
        return self._data(
            await self._request("GET", f"/api/v2/clients/{client_id}/jobs")
        ) or []

    async def get_client_completed_jobs(self, client_id: str) -> list[dict[str, Any]]:
        """List a client's completed jobs (``GET /api/v2/clients/{id}/completed-jobs``)."""
        return self._data(
            await self._request("GET", f"/api/v2/clients/{client_id}/completed-jobs")
        ) or []

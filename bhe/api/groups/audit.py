"""Audit API group - the BHE platform audit log.

Read-only: the audit log records actions taken in the BHE tenant (logins, token
creation, config changes, ...), which is what powers ``bhe audit`` for "who
logged in / acted, and when".
"""

from __future__ import annotations

from typing import Any

from bhe.api.groups._base import GroupMixin


class AuditMixin(GroupMixin):
    """Read access to the platform audit log."""

    async def get_audit_log(
        self, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Fetch audit-log entries (``GET /api/v2/audit``).

        Common params: ``after`` / ``before`` (RFC3339), ``limit``, ``skip``,
        ``sort_by`` (e.g. ``-created_at``).  The envelope varies by BHE version
        (``{"data": [...]}`` or ``{"data": {"logs": [...]}}``); the caller unwraps.
        """
        return await self._request("GET", "/api/v2/audit", params=params)

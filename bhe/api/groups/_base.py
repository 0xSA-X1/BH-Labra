"""Shared base for API-group mixins.

The concrete :meth:`_request` is supplied by ``BHETransport`` at composition
time; this base only declares the signature so each mixin is self-describing and
type-checkable in isolation.
"""

from __future__ import annotations

from typing import Any


class GroupMixin:
    """Base declaring the transport hook every API-group mixin relies on."""

    async def _request(
        self,
        method: str,
        endpoint: str,
        json_data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:  # pragma: no cover - overridden by BHETransport
        raise NotImplementedError(
            "GroupMixin must be composed with BHETransport, which provides _request()."
        )

    @staticmethod
    def _data(response: dict[str, Any]) -> Any:
        """Unwrap BHE's common ``{"data": ...}`` envelope, else return as-is."""
        if isinstance(response, dict) and "data" in response:
            return response["data"]
        return response

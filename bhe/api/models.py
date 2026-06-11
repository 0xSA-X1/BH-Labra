"""Pydantic models for BHE API responses.

These are intentionally lenient (``extra="allow"``): the BHE API returns rich,
version-dependent objects and bhe only needs a stable subset for display.
Unmodelled fields pass through untouched so power users can still see them in
the raw views.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Lenient(BaseModel):
    """Base model that tolerates and preserves unmodelled fields."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class Domain(_Lenient):
    """An AD/AzureAD domain or tenant known to BHE (``/available-domains``)."""

    id: str | None = None
    name: str | None = None
    type: str | None = None
    collected: bool | None = None
    impact_value: int | None = Field(default=None, alias="impactValue")


class PostureStat(_Lenient):
    """A single risk-posture data point (``/posture-stats``)."""

    domain_sid: str | None = None
    exposure_index: float | None = None
    tier_zero_count: int | None = None
    critical_risk_count: int | None = None
    finding: str | None = None
    created_at: str | None = None


class Finding(_Lenient):
    """An attack-path finding for a domain (``/attack-path-findings``)."""

    id: Any | None = None
    finding: str | None = None
    domain_sid: str | None = None
    principal: str | None = None
    principal_kind: str | None = None
    accepted: bool | None = None
    severity: str | None = None
    exposure: float | None = None


class Client(_Lenient):
    """A registered collection client - SharpHound/AzureHound Enterprise."""

    id: str | None = None
    name: str | None = None
    type: str | None = None
    version: str | None = None
    last_checkin: str | None = None
    ip_address: str | None = None
    hostname: str | None = None


class Job(_Lenient):
    """A collection job run by a client (``/jobs``)."""

    id: Any | None = None
    client_id: str | None = None
    status: Any | None = None
    status_message: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    event_id: Any | None = None


class Event(_Lenient):
    """A scheduled collection event (``/events``)."""

    id: Any | None = None
    client_id: str | None = None
    rrule: str | None = None
    session_collection: bool | None = None
    ad_structure_collection: bool | None = None
    local_group_collection: bool | None = None
    next_scheduled_at: str | None = None


class MetaEntity(_Lenient):
    """A meta entity / aggregate node (``/meta/{id}``)."""

    object_id: str | None = None
    kind: str | None = None
    name: str | None = None
    props: dict[str, Any] | None = None


class GraphNode(_Lenient):
    """A node in a BHE graph/Cypher response.

    ``graph_id`` is the key the node had in the response's ``nodes`` map - this
    is what ``edges`` reference, so it (not ``object_id``) is used to link a
    graph back into paths.  ``object_id`` is the node's own SID, for display.
    """

    object_id: str | None = Field(default=None, alias="objectId")
    graph_id: str | None = None
    label: str | None = None
    kind: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)

    @property
    def link_id(self) -> str | None:
        """Identifier edges use to reference this node."""
        return self.graph_id or self.object_id


class GraphEdge(_Lenient):
    """An edge in a BHE graph/Cypher response."""

    source: str | None = None
    target: str | None = None
    label: str | None = None
    kind: str | None = None


class Path(_Lenient):
    """An ordered attack path: alternating node/edge sequence."""

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)

    def length(self) -> int:
        """Number of hops (edges) in the path."""
        return len(self.edges)

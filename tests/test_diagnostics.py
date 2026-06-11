"""Diagnostics engine tests — mock-backed, network-free, deterministic clock."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from bhe.api.client import BHEClient
from bhe.config import TenantProfile
from bhe.diagnostics import Status, run_diagnostics

# Mock collector check-ins are 2026-06-04; freeze "now" just after so freshness
# checks are deterministic regardless of the real date.
FIXED_NOW = datetime(2026, 6, 4, 10, 0, tzinfo=timezone.utc)
FUTURE_NOW = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)


def _profile() -> TenantProfile:
    return TenantProfile(name="test", base_url="https://mock", mock=True)


async def _report(now=FIXED_NOW):
    profile = _profile()
    async with BHEClient.connect(
        profile.base_url, profile.token_id, profile.token_key or "", mock=True
    ) as client:
        return await run_diagnostics(client, profile, now=now)


async def test_diagnostics_cover_core_checks() -> None:
    report = await _report()
    ids = {c.id for c in report.checks}
    assert {
        "connectivity", "role", "version", "domains",
        "collectors", "jobs", "schedules", "posture", "exposure",
    } <= ids


async def test_connectivity_and_role_ok_on_mock() -> None:
    by_id = {c.id: c for c in (await _report()).checks}
    assert by_id["connectivity"].status is Status.OK
    assert by_id["role"].status is Status.OK  # mock self reports "Read-Only"
    # Both mock collectors checked in within the freshness window at FIXED_NOW.
    assert by_id["collectors"].status is Status.OK


async def test_stale_collectors_flagged_as_failure() -> None:
    report = await _report(now=FUTURE_NOW)
    collectors = next(c for c in report.checks if c.id == "collectors")
    assert collectors.status is Status.FAIL
    assert report.has_failures is True
    assert report.worst is Status.FAIL


async def test_report_serialization_roundtrips() -> None:
    report = await _report()
    assert "Overall:" in report.render_text()
    payload = json.loads(report.to_json())
    assert payload["worst"] in {s.value for s in Status}
    assert payload["checks"]
    assert payload["checks"][0]["status"] in {s.value for s in Status}


async def test_connectivity_failure_is_isolated() -> None:
    """A failing identity call must yield a FAIL check, not raise."""

    class _BrokenClient:
        async def get_self(self):
            raise RuntimeError("401 Unauthorized")

        async def _empty(self, *a, **k):
            return {}

        def __getattr__(self, _name):  # any other endpoint -> empty/no-op
            async def _noop(*a, **k):
                return {}
            return _noop

    report = await run_diagnostics(_BrokenClient(), _profile(), now=FIXED_NOW)
    conn = next(c for c in report.checks if c.id == "connectivity")
    assert conn.status is Status.FAIL
    assert "401" in conn.detail

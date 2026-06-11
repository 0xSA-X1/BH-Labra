"""Tenant health diagnostics - the engine behind the Doctor tab and CLI.

``run_diagnostics`` issues a battery of *read-only* requests against a connected
:class:`~bhe.api.client.BHEClient` and returns a :class:`DiagnosticsReport`
of pass/warn/fail :class:`Check` results, each with a human summary and (where
relevant) a remediation hint.

Design notes:
- Every check degrades gracefully: an unexpected error becomes a ``FAIL`` check
  rather than an exception, so a single broken endpoint never aborts the report.
- The engine is UI-agnostic - the TUI renders the report into a table, the CLI
  prints it and maps it to an exit code, and tests assert on it directly.
- ``now`` is injectable so freshness checks (collector check-in, stuck jobs) are
  deterministic under test.
"""

from __future__ import annotations

import json as _json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, TypeVar

T = TypeVar("T")

# Freshness thresholds (hours).
COLLECTOR_WARN_HRS = 24
COLLECTOR_FAIL_HRS = 72
JOB_STUCK_HRS = 12
# Tier Zero exposure at/above this is flagged for attention (percent).
HIGH_EXPOSURE_PCT = 90.0


class Status(str, Enum):
    """Per-check outcome. String-valued so it serializes cleanly to JSON."""

    OK = "OK"
    INFO = "INFO"
    SKIP = "SKIP"
    WARN = "WARN"
    FAIL = "FAIL"


# Higher rank = worse; drives the report's overall status and exit code.
_SEVERITY: dict[Status, int] = {
    Status.OK: 0,
    Status.INFO: 0,
    Status.SKIP: 0,
    Status.WARN: 1,
    Status.FAIL: 2,
}


@dataclass(frozen=True)
class Check:
    """One diagnostic result."""

    id: str
    title: str
    status: Status
    summary: str
    detail: str = ""
    remediation: str = ""


@dataclass
class DiagnosticsReport:
    """The full set of checks plus convenience rollups."""

    checks: list[Check] = field(default_factory=list)

    @property
    def worst(self) -> Status:
        if not self.checks:
            return Status.INFO
        return max((c.status for c in self.checks), key=lambda s: _SEVERITY[s])

    @property
    def has_failures(self) -> bool:
        return any(c.status is Status.FAIL for c in self.checks)

    @property
    def has_warnings(self) -> bool:
        return any(c.status is Status.WARN for c in self.checks)

    def counts(self) -> dict[Status, int]:
        out: dict[Status, int] = {s: 0 for s in Status}
        for c in self.checks:
            out[c.status] += 1
        return out

    def to_json(self) -> str:
        payload = {
            "worst": self.worst.value,
            "counts": {s.value: n for s, n in self.counts().items() if n},
            "checks": [
                {**asdict(c), "status": c.status.value} for c in self.checks
            ],
        }
        return _json.dumps(payload, indent=2)

    def render_text(self) -> str:
        """Plain-text report for the CLI (no Rich markup)."""
        symbol = {
            Status.OK: "[ OK ]",
            Status.INFO: "[INFO]",
            Status.SKIP: "[SKIP]",
            Status.WARN: "[WARN]",
            Status.FAIL: "[FAIL]",
        }
        lines: list[str] = []
        for c in self.checks:
            lines.append(f"{symbol[c.status]}  {c.title}: {c.summary}")
            if c.status in (Status.WARN, Status.FAIL) and c.remediation:
                lines.append(f"         -> {c.remediation}")
        counts = {s.value: n for s, n in self.counts().items() if n}
        summary = "  ".join(f"{k}={v}" for k, v in counts.items())
        lines.append("")
        lines.append(f"Overall: {self.worst.value}   ({summary})")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _safe(coro: Awaitable[T]) -> tuple[T | None, Exception | None]:
    """Await a coroutine, capturing any exception instead of raising."""
    try:
        return await coro, None
    except Exception as exc:  # noqa: BLE001 - we deliberately surface everything as a Check
        return None, exc


def _unwrap(value: Any) -> Any:
    """Peel BHE's ``{"data": ...}`` envelope if present."""
    if isinstance(value, dict) and "data" in value:
        return value["data"]
    return value


def _parse_dt(value: Any) -> datetime | None:
    """Parse an RFC3339 timestamp (``...Z``) to an aware datetime, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _age_hours(value: Any, now: datetime) -> float | None:
    dt = _parse_dt(value)
    if dt is None:
        return None
    return (now - dt).total_seconds() / 3600.0


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

async def run_diagnostics(
    client: Any, profile: Any, *, now: datetime | None = None
) -> DiagnosticsReport:
    """Run all health checks against ``client`` and return a report.

    Args:
        client: A connected ``BHEClient`` (read-only).
        profile: The active ``TenantProfile`` (for context in messages).
        now: Reference time for freshness checks; defaults to UTC now.
    """
    now = now or datetime.now(timezone.utc)

    # Gather everything once (each call is independent and error-isolated).
    self_v, self_e = await _safe(client.get_self())
    ver_v, ver_e = await _safe(client.get_version())
    spec_v, _spec_e = await _safe(client.discover_spec())
    dom_v, dom_e = await _safe(client.get_available_domains())
    cli_v, cli_e = await _safe(client.get_clients())
    job_v, job_e = await _safe(client.get_jobs())
    evt_v, evt_e = await _safe(client.get_events())
    pos_v, pos_e = await _safe(client.get_posture_stats())

    checks: list[Check] = [
        _check_connectivity(self_v, self_e),
        _check_role(self_v, self_e),
        _check_version(ver_v, ver_e),
        _check_spec(spec_v),
        _check_domains(dom_v, dom_e),
        _check_collectors(cli_v, cli_e, now),
        _check_jobs(job_v, job_e, now),
        _check_schedules(evt_v, evt_e, cli_v),
        _check_posture(pos_v, pos_e),
        _check_exposure(pos_v),
    ]
    return DiagnosticsReport(checks=checks)


def _check_connectivity(self_v: Any, self_e: Exception | None) -> Check:
    if self_e is not None or self_v is None:
        return Check(
            "connectivity", "Connectivity & authentication", Status.FAIL,
            "Could not reach the tenant or authenticate",
            detail=str(self_e) if self_e else "empty response",
            remediation="Check BHE_TENANT_URL, the Token ID/Key, the token's "
            "role, and network egress to the tenant.",
        )
    data = _unwrap(self_v) or {}
    who = data.get("principal_name") or "?"
    return Check(
        "connectivity", "Connectivity & authentication", Status.OK,
        f"Authenticated as {who}",
        detail=f"roles={data.get('roles')}",
    )


def _check_role(self_v: Any, self_e: Exception | None) -> Check:
    if self_e is not None or self_v is None:
        return Check("role", "API token role", Status.SKIP,
                     "Skipped (no identity)", detail="self lookup failed")
    roles = [str(r) for r in (_unwrap(self_v) or {}).get("roles") or []]
    if not roles:
        return Check("role", "API token role", Status.WARN,
                     "No role reported for this token",
                     remediation="Confirm the API user has a role assigned.")
    if any("read" in r.lower() and "only" in r.lower() for r in roles):
        return Check("role", "API token role", Status.OK,
                     f"Read-Only role ({', '.join(roles)})")
    return Check(
        "role", "API token role", Status.WARN,
        f"Token has elevated role(s): {', '.join(roles)}",
        remediation="For read-only scouting, provision the token under a "
        "Read-Only BHE role to minimise blast radius.",
    )


def _check_version(ver_v: Any, ver_e: Exception | None) -> Check:
    if ver_e is not None or ver_v is None:
        return Check("version", "Server version", Status.WARN,
                     "Could not read server version", detail=str(ver_e or ""))
    data = _unwrap(ver_v)
    ver = None
    if isinstance(data, dict):
        api = data.get("API")
        if isinstance(api, dict):
            ver = api.get("version")
        ver = ver or data.get("version") or data.get("server_version")
    return Check("version", "Server version", Status.INFO,
                 f"BHE version {ver or 'unknown'}")


def _check_spec(spec_v: Any) -> Check:
    if isinstance(spec_v, dict) and spec_v:
        paths = spec_v.get("paths")
        n = len(paths) if isinstance(paths, dict) else 0
        return Check("spec", "OpenAPI spec (route confirmation)", Status.OK,
                     f"Spec reachable ({n} paths)")
    return Check("spec", "OpenAPI spec (route confirmation)", Status.SKIP,
                 "Tenant does not expose an OpenAPI spec",
                 remediation="Older BHE versions may not serve the spec; route "
                 "drift can't be auto-confirmed.")


def _check_domains(dom_v: Any, dom_e: Exception | None) -> Check:
    if dom_e is not None or dom_v is None:
        return Check("domains", "Domains / environments", Status.FAIL,
                     "Could not list domains", detail=str(dom_e or ""),
                     remediation="Verify the token role can read available-domains.")
    domains = [d for d in dom_v if isinstance(d, dict)]
    if not domains:
        return Check("domains", "Domains / environments", Status.FAIL,
                     "No domains/environments are known to this tenant",
                     remediation="Ensure at least one collector has uploaded data.")
    collected = [d for d in domains if d.get("collected")]
    if not collected:
        return Check(
            "domains", "Domains / environments", Status.WARN,
            f"{len(domains)} domain(s) known, none marked collected",
            remediation="Run/await a collection; uncollected domains yield no paths.",
        )
    return Check("domains", "Domains / environments", Status.OK,
                 f"{len(collected)}/{len(domains)} domain(s) collected")


def _check_collectors(cli_v: Any, cli_e: Exception | None, now: datetime) -> Check:
    if cli_e is not None:
        return Check("collectors", "Collector check-in", Status.WARN,
                     "Could not list collection clients", detail=str(cli_e))
    clients = [c for c in (cli_v or []) if isinstance(c, dict)]
    if not clients:
        return Check("collectors", "Collector check-in", Status.SKIP,
                     "No managed collection clients registered",
                     remediation="Tenants using only file uploads have no clients.")
    worst = Status.OK
    stale: list[str] = []
    unknown: list[str] = []
    for c in clients:
        name = c.get("name") or c.get("id") or "?"
        age = _age_hours(c.get("last_checkin"), now)
        if age is None:
            unknown.append(name)
            continue
        if age >= COLLECTOR_FAIL_HRS:
            stale.append(f"{name} ({age:.0f}h)")
            worst = Status.FAIL
        elif age >= COLLECTOR_WARN_HRS:
            stale.append(f"{name} ({age:.0f}h)")
            if worst is not Status.FAIL:
                worst = Status.WARN
    if worst is Status.OK and not unknown:
        return Check("collectors", "Collector check-in", Status.OK,
                     f"All {len(clients)} collector(s) checked in recently")
    summary_bits = []
    if stale:
        summary_bits.append(f"{len(stale)} stale: {', '.join(stale)}")
    if unknown:
        summary_bits.append(f"{len(unknown)} never checked in: {', '.join(unknown)}")
    return Check(
        "collectors", "Collector check-in",
        worst if worst is not Status.OK else Status.WARN,
        "; ".join(summary_bits) or "Collector check-in issues",
        remediation="Confirm the collector service is running and can reach the "
        f"tenant. Warn >{COLLECTOR_WARN_HRS}h, fail >{COLLECTOR_FAIL_HRS}h since check-in.",
    )


def _check_jobs(job_v: Any, job_e: Exception | None, now: datetime) -> Check:
    if job_e is not None:
        return Check("jobs", "Collection jobs", Status.WARN,
                     "Could not list jobs", detail=str(job_e))
    jobs = [j for j in (job_v or []) if isinstance(j, dict)]
    if not jobs:
        return Check("jobs", "Collection jobs", Status.SKIP, "No collection jobs found")
    failed = [j for j in jobs if str(j.get("status", "")).upper() in {"FAILED", "ERROR", "CANCELED", "CANCELLED"}]
    stuck = []
    for j in jobs:
        if str(j.get("status", "")).upper() == "RUNNING":
            age = _age_hours(j.get("start_time"), now)
            if age is not None and age >= JOB_STUCK_HRS:
                stuck.append(f"#{j.get('id')} ({age:.0f}h)")
    if failed:
        return Check(
            "jobs", "Collection jobs", Status.WARN,
            f"{len(failed)} of {len(jobs)} recent job(s) failed/canceled",
            detail=", ".join(f"#{j.get('id')}: {j.get('status_message') or j.get('status')}" for j in failed[:5]),
            remediation="Inspect the failing job(s) and collector logs; re-run "
            "collection once the cause is resolved.",
        )
    if stuck:
        return Check("jobs", "Collection jobs", Status.WARN,
                     f"{len(stuck)} job(s) running >{JOB_STUCK_HRS}h: {', '.join(stuck)}",
                     remediation="A long-running job may be stuck; check the collector.")
    return Check("jobs", "Collection jobs", Status.OK,
                 f"{len(jobs)} job(s), none failed or stuck")


def _check_schedules(evt_v: Any, evt_e: Exception | None, cli_v: Any) -> Check:
    if evt_e is not None:
        return Check("schedules", "Collection schedules", Status.WARN,
                     "Could not list schedules", detail=str(evt_e))
    events = [e for e in (evt_v or []) if isinstance(e, dict)]
    has_clients = bool([c for c in (cli_v or []) if isinstance(c, dict)])
    if events:
        return Check("schedules", "Collection schedules", Status.OK,
                     f"{len(events)} schedule(s) configured")
    if has_clients:
        return Check("schedules", "Collection schedules", Status.WARN,
                     "Collectors exist but no schedules are configured",
                     remediation="Without a schedule, collection won't recur; "
                     "configure an Event so data stays fresh.")
    return Check("schedules", "Collection schedules", Status.SKIP,
                 "No schedules (no managed collectors)")


def _check_posture(pos_v: Any, pos_e: Exception | None) -> Check:
    if pos_e is not None or pos_v is None:
        return Check("posture", "Risk posture data", Status.WARN,
                     "Could not read posture stats", detail=str(pos_e or ""))
    rows = _posture_items(pos_v)
    if not rows:
        return Check("posture", "Risk posture data", Status.WARN,
                     "No posture data returned",
                     remediation="Posture appears after collection + analysis; "
                     "if collection is recent, allow analysis to complete.")
    return Check("posture", "Risk posture data", Status.OK,
                 f"Posture data present for {len(rows)} domain(s)")


def _check_exposure(pos_v: Any) -> Check:
    rows = _posture_items(pos_v)
    if not rows:
        return Check("exposure", "Tier Zero exposure", Status.SKIP, "No exposure data")
    hot: list[str] = []
    for item in rows:
        exp = item.get("exposure_index", item.get("exposure"))
        try:
            val = float(exp)
        except (TypeError, ValueError):
            continue
        if val >= HIGH_EXPOSURE_PCT:
            hot.append(f"{item.get('domain_sid') or item.get('domain') or '?'} ({val:.0f}%)")
    if hot:
        return Check("exposure", "Tier Zero exposure", Status.WARN,
                     f"{len(hot)} domain(s) at/above {HIGH_EXPOSURE_PCT:.0f}% exposure: "
                     + ", ".join(hot[:5]),
                     remediation="High Tier Zero exposure - prioritise the top "
                     "attack-path findings for these domains.")
    return Check("exposure", "Tier Zero exposure", Status.OK,
                 f"No domain at/above {HIGH_EXPOSURE_PCT:.0f}% exposure")


def _posture_items(pos_v: Any) -> list[dict[str, Any]]:
    data = _unwrap(pos_v)
    if isinstance(data, dict):
        data = data.get("data", [])
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]

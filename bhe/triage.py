"""Cross-domain triage: rank a multi-domain estate's findings into a 'start here' list.

BHE exposes a domain's findings at ``GET /domains/{id}/details?finding=<type>``.
The paginated envelope's ``count`` is the affected-principal total, and each record
carries ``Severity`` and ``ImpactPercentage`` - so one cheap ``limit=1`` call per
finding type yields everything we need to rank, without paging huge ``Props`` blobs.

This module is pure and UI-agnostic (like :mod:`bhe.diagnostics`): it takes
per-(domain, finding-type) aggregates and ranks them across the whole estate - the
cross-domain "where to start" view BHE's per-domain UI lacks.

Scoring is intentionally simple and transparent so it's defensible to a customer:

    score = severity_weight * affected_principals * (1 + impact)

where ``impact`` is BHE's ImpactPercentage (blast radius) for a representative
principal of that finding.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

# Severity → weight.  Spread so a single critical outranks a pile of lows, but a
# large population of highs can still surface above one low-impact critical.
SEVERITY_WEIGHT: dict[str, int] = {
    "critical": 100,
    "high": 10,
    "medium": 3,
    "low": 1,
}


def severity_rank(severity: str) -> int:
    return SEVERITY_WEIGHT.get((severity or "").lower(), 0)


def finding_score(severity: str, principals: int, impact: float) -> float:
    """The transparent prioritisation score (see module docstring)."""
    return severity_rank(severity) * max(principals, 0) * (1.0 + max(impact, 0.0))


@dataclass(slots=True)
class FindingStat:
    """One (finding-type, domain) aggregate, scored for prioritisation."""

    finding: str
    domain: str
    severity: str
    principals: int       # affected-principal total (the /details envelope `count`)
    impact: float         # representative ImpactPercentage (0..1)
    exposure: float = 0.0  # representative ExposurePercentage (0..1; needs butterfly analysis)

    @property
    def score(self) -> float:
        return finding_score(self.severity, self.principals, self.impact)

    def as_dict(self) -> dict[str, Any]:
        return {
            "finding": self.finding,
            "domain": self.domain,
            "severity": self.severity,
            "principals": self.principals,
            "exposure": round(self.exposure, 3),
            "impact": round(self.impact, 3),
            "score": round(self.score, 1),
        }


def rank(stats: Iterable[FindingStat]) -> list[FindingStat]:
    """Worst-first ranking across the estate."""
    return sorted(stats, key=lambda s: s.score, reverse=True)


def rollup_by_type(stats: Iterable[FindingStat]) -> list[dict[str, Any]]:
    """Collapse per-domain stats into one row per finding type (estate-wide)."""
    agg: dict[str, dict[str, Any]] = {}
    for s in stats:
        b = agg.setdefault(
            s.finding,
            {"finding": s.finding, "severity": s.severity, "domains": set(),
             "principals": 0, "impact": 0.0},
        )
        b["domains"].add(s.domain)
        b["principals"] += s.principals
        b["impact"] = max(b["impact"], s.impact)
        if severity_rank(s.severity) > severity_rank(b["severity"]):
            b["severity"] = s.severity
    out = [
        {
            "finding": b["finding"],
            "severity": b["severity"],
            "domains": len(b["domains"]),
            "principals": b["principals"],
            "impact": round(b["impact"], 3),
            "score": round(finding_score(b["severity"], b["principals"], b["impact"]), 1),
        }
        for b in agg.values()
    ]
    out.sort(key=lambda d: d["score"], reverse=True)
    return out


def severity_totals(stats: Iterable[FindingStat]) -> dict[str, int]:
    """Count affected principals by severity across all stats (for a summary line)."""
    totals: dict[str, int] = defaultdict(int)
    for s in stats:
        totals[(s.severity or "").lower()] += s.principals
    return dict(totals)

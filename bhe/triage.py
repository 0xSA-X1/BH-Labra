"""Cross-domain triage: turn BHE's precomputed findings into a 'start here' list.

BHE already runs the expensive attack-path analysis and exposes the result per
domain (``attack-path-findings``).  The gap this fills is that the BHE UI won't
rank findings *across* a 10+ domain estate in one view.  This module is pure and
UI-agnostic (like :mod:`bhe.diagnostics`): it takes ``(domain, findings)`` pairs
and produces a ranked, scored prioritisation — no Cypher, no timeout risk.

Scoring is intentionally simple and transparent so it's defensible to a customer:

    score = severity_weight * active_principals * (1 + max_exposure)

where accepted-risk findings are excluded by default (they're a deliberate
business decision, not unremediated exposure).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Iterable

# Severity → weight.  Spread so a single critical outranks a pile of lows, but a
# large population of highs can still surface above one low-exposure critical.
SEVERITY_WEIGHT: dict[str, int] = {
    "critical": 100,
    "high": 10,
    "medium": 3,
    "low": 1,
}


def severity_rank(severity: str) -> int:
    return SEVERITY_WEIGHT.get((severity or "").lower(), 0)


def score(severity: str, principals: int, max_exposure: float) -> float:
    """The transparent prioritisation score (see module docstring)."""
    return severity_rank(severity) * max(principals, 0) * (1.0 + max_exposure)


@dataclass(slots=True)
class TriageRow:
    """One (finding-type, domain) bucket, scored for prioritisation."""

    finding: str
    domain: str
    severity: str
    principals: int          # active (non-accepted) principals with this finding
    accepted: int            # how many were accepted-risk (shown, not scored)
    max_exposure: float
    score: float

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["max_exposure"] = round(self.max_exposure, 3)
        d["score"] = round(self.score, 1)
        return d


def _findings_list(findings: Any) -> list[dict[str, Any]]:
    """Accept either the raw ``{"data": [...]}`` envelope or a bare list."""
    if isinstance(findings, dict):
        return findings.get("data", []) or []
    return findings or []


def summarize(
    per_domain: Iterable[tuple[dict[str, Any], Any]],
    *,
    include_accepted: bool = False,
) -> list[TriageRow]:
    """Rank findings across domains, worst first.

    Args:
        per_domain: iterable of ``(domain_record, findings)`` where ``findings``
            is the attack-path-findings payload (envelope or list).
        include_accepted: if False (default), accepted-risk findings are excluded
            from the score (but still counted in the ``accepted`` column).
    """
    rows: list[TriageRow] = []
    for domain, raw in per_domain:
        domain_name = domain.get("name", domain.get("id", "?"))
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for f in _findings_list(raw):
            groups[f.get("finding", "?")].append(f)

        for finding, items in groups.items():
            active = [i for i in items if not i.get("accepted")]
            accepted = len(items) - len(active)
            scored = items if include_accepted else active
            if not scored:
                continue
            worst = max((i.get("severity", "low") for i in scored), key=severity_rank)
            principals = len(scored)
            max_exp = max((float(i.get("exposure") or 0.0) for i in scored), default=0.0)
            rows.append(
                TriageRow(
                    finding=finding,
                    domain=domain_name,
                    severity=worst,
                    principals=principals,
                    accepted=accepted,
                    max_exposure=max_exp,
                    score=score(worst, principals, max_exp),
                )
            )
    rows.sort(key=lambda r: r.score, reverse=True)
    return rows


def rollup_by_type(rows: Iterable[TriageRow]) -> list[dict[str, Any]]:
    """Collapse per-domain rows into one row per finding type (estate-wide)."""
    agg: dict[str, dict[str, Any]] = {}
    for r in rows:
        b = agg.setdefault(
            r.finding,
            {"finding": r.finding, "severity": r.severity, "domains": set(),
             "principals": 0, "max_exposure": 0.0, "score": 0.0},
        )
        b["domains"].add(r.domain)
        b["principals"] += r.principals
        b["max_exposure"] = max(b["max_exposure"], r.max_exposure)
        b["score"] += r.score
        if severity_rank(r.severity) > severity_rank(b["severity"]):
            b["severity"] = r.severity
    out = [
        {
            "finding": b["finding"],
            "severity": b["severity"],
            "domains": len(b["domains"]),
            "principals": b["principals"],
            "max_exposure": round(b["max_exposure"], 3),
            "score": round(b["score"], 1),
        }
        for b in agg.values()
    ]
    out.sort(key=lambda d: d["score"], reverse=True)
    return out


def severity_totals(rows: Iterable[TriageRow]) -> dict[str, int]:
    """Count active principals by severity across all rows (for a summary line)."""
    totals: dict[str, int] = defaultdict(int)
    for r in rows:
        totals[r.severity.lower()] += r.principals
    return dict(totals)

"""Tests for the cross-domain triage scoring engine (aggregate model)."""

from __future__ import annotations

from bhe.triage import (
    FindingStat,
    finding_score,
    rank,
    rollup_by_type,
    severity_rank,
    severity_totals,
)


def _stat(finding, domain, severity, principals, impact) -> FindingStat:
    return FindingStat(
        finding=finding, domain=domain, severity=severity,
        principals=principals, impact=impact,
    )


# An estate: CORP has a critical (DCSync) + a high (Kerberoasting); DEV has the
# same high (fewer principals) + a medium.
_STATS = [
    _stat("DCSync", "CORP.LOCAL", "critical", 2, 0.9),
    _stat("Kerberoasting", "CORP.LOCAL", "high", 5, 0.3),
    _stat("Kerberoasting", "DEV.LOCAL", "high", 1, 0.1),
    _stat("ASREPRoasting", "DEV.LOCAL", "medium", 3, 0.2),
]


def test_finding_score_formula() -> None:
    # critical(100) * 2 principals * (1 + 0.5 impact) = 300
    assert finding_score("critical", 2, 0.5) == 300
    assert finding_score("low", 1, 0.0) == 1
    assert finding_score("unknown", 5, 0.9) == 0  # unknown severity -> weight 0


def test_severity_rank_orders() -> None:
    assert severity_rank("critical") > severity_rank("high")
    assert severity_rank("high") > severity_rank("medium") > severity_rank("low")
    assert severity_rank("bogus") == 0


def test_rank_puts_critical_first() -> None:
    ranked = rank(_STATS)
    assert ranked[0].finding == "DCSync"  # 100 * 2 * 1.9 = 380, the top
    scores = [s.score for s in ranked]
    assert scores == sorted(scores, reverse=True)


def test_rollup_by_type_collapses_domains() -> None:
    rolled = rollup_by_type(_STATS)
    kerb = next(r for r in rolled if r["finding"] == "Kerberoasting")
    assert kerb["domains"] == 2          # CORP + DEV
    assert kerb["principals"] == 6       # 5 + 1
    assert kerb["impact"] == 0.3         # max across domains
    assert rolled[0]["finding"] == "DCSync"  # critical still ranks first


def test_severity_totals_counts_principals() -> None:
    totals = severity_totals(_STATS)
    assert totals["critical"] == 2
    assert totals["high"] == 6  # 5 CORP + 1 DEV
    assert totals["medium"] == 3

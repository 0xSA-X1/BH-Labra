"""Tests for the cross-domain triage scoring engine."""

from __future__ import annotations

from bhe.triage import (
    rollup_by_type,
    score,
    severity_totals,
    summarize,
)

# Two domains; CORP has a critical (DCSync) + a high (Kerberoastable) + an
# accepted high (Unconstrained); DEV has only the high.
_CORP = (
    {"name": "CORP.LOCAL", "id": "S-1-corp"},
    {
        "data": [
            {"finding": "DCSync", "severity": "critical", "accepted": False, "exposure": 0.9},
            {"finding": "Kerberoastable", "severity": "high", "accepted": False, "exposure": 0.3},
            {"finding": "Kerberoastable", "severity": "high", "accepted": False, "exposure": 0.5},
            {"finding": "Unconstrained", "severity": "high", "accepted": True, "exposure": 0.2},
        ]
    },
)
_DEV = (
    {"name": "DEV.LOCAL", "id": "S-1-dev"},
    [{"finding": "Kerberoastable", "severity": "high", "accepted": False, "exposure": 0.1}],
)


def test_score_formula() -> None:
    # critical(100) * 2 principals * (1 + 0.5 exposure) = 300
    assert score("critical", 2, 0.5) == 300
    assert score("low", 1, 0.0) == 1
    assert score("unknown", 5, 0.9) == 0  # unknown severity -> weight 0


def test_summarize_ranks_critical_first_and_excludes_accepted() -> None:
    rows = summarize([_CORP, _DEV])
    # DCSync (critical) must outrank everything.
    assert rows[0].finding == "DCSync"
    # The accepted Unconstrained finding is not scored as active.
    assert not any(r.finding == "Unconstrained" for r in rows)
    # CORP Kerberoastable groups its 2 active principals, max_exposure 0.5.
    corp_kerb = next(r for r in rows if r.finding == "Kerberoastable" and r.domain == "CORP.LOCAL")
    assert corp_kerb.principals == 2
    assert corp_kerb.max_exposure == 0.5


def test_accepted_counted_but_not_scored() -> None:
    rows = summarize([_CORP, _DEV])
    # Unconstrained is all-accepted -> excluded entirely by default.
    assert not any(r.finding == "Unconstrained" for r in rows)
    # With include_accepted it appears.
    rows2 = summarize([_CORP, _DEV], include_accepted=True)
    assert any(r.finding == "Unconstrained" for r in rows2)


def test_rollup_by_type_collapses_domains() -> None:
    rows = summarize([_CORP, _DEV])
    rolled = rollup_by_type(rows)
    kerb = next(r for r in rolled if r["finding"] == "Kerberoastable")
    assert kerb["domains"] == 2          # CORP + DEV
    assert kerb["principals"] == 3       # 2 + 1
    assert kerb["max_exposure"] == 0.5
    # DCSync (critical) still ranks first overall.
    assert rolled[0]["finding"] == "DCSync"


def test_severity_totals() -> None:
    totals = severity_totals(summarize([_CORP, _DEV]))
    assert totals["critical"] == 1
    assert totals["high"] == 3  # 2 CORP + 1 DEV active kerberoastable principals

"""CLI smoke tests via Typer's runner, all mock-backed (no network)."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from bhe import __version__
from bhe.cli import app

runner = CliRunner()


def _invoke(*args: str):
    # Global flags (e.g. --mock) come before the subcommand.
    return runner.invoke(app, list(args))


def test_version_flag() -> None:
    result = _invoke("--version")
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_no_args_shows_help() -> None:
    result = _invoke()
    assert result.exit_code != 0  # no_args_is_help -> usage, non-zero
    assert "Usage" in result.stdout or "Commands" in result.stdout


def test_domains_json() -> None:
    result = _invoke("--mock", "--json", "domains")
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert len(data) == 3


def test_jobs_json() -> None:
    result = _invoke("--mock", "--json", "jobs")
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert any(j.get("status") == "RUNNING" for j in data)


def test_get_escape_hatch() -> None:
    result = _invoke("--mock", "get", "/api/v2/available-domains")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "data" in payload and len(payload["data"]) == 3


def test_get_normalises_missing_leading_slash() -> None:
    result = _invoke("--mock", "get", "api/v2/available-domains")
    assert result.exit_code == 0
    assert "data" in json.loads(result.stdout)


def test_cypher_read_query_json() -> None:
    result = _invoke("--mock", "--json", "cypher", "MATCH (u:User) RETURN u LIMIT 5")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert "data" in payload


def test_cypher_write_is_blocked() -> None:
    result = _invoke("--mock", "cypher", "MATCH (n) DETACH DELETE n")
    assert result.exit_code == 1  # ReadOnlyViolation -> clean non-zero exit


def test_domains_table_renders() -> None:
    # Default (non-JSON) path should produce a table without error.
    result = _invoke("--mock", "domains")
    assert result.exit_code == 0
    assert "row(s)" in result.stdout


def test_domains_drilldown_json() -> None:
    result = _invoke("--mock", "--json", "domains", "contoso")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["type"] == "azure"
    assert payload["name"] == "contoso.onmicrosoft.com"


def test_domains_ambiguous_exits_2() -> None:
    result = _invoke("--mock", "domains", "corp")  # CORP.LOCAL + DEV.CORP.LOCAL
    assert result.exit_code == 2


def test_findings_by_domain_name() -> None:
    result = _invoke("--mock", "--json", "findings", "CORP.LOCAL")
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    # Per finding-type counts (available-types -> findings per type).
    assert any(r["finding"] == "Kerberoastable" and r["principals"] >= 1 for r in rows)


def test_jobs_correlated_to_client() -> None:
    result = _invoke("--mock", "--json", "jobs")
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    job_101 = next(r for r in rows if r["id"] == 101)
    assert job_101["client"] == "DC01-Collector"  # GUID resolved to a name
    assert job_101["hostname"] == "DC01.CORP.LOCAL"


def test_entity_by_name_exact() -> None:
    result = _invoke("--mock", "--json", "entity", "ALICE@CORP.LOCAL")
    assert result.exit_code == 0
    # Resolves the name -> objectid -> user detail envelope.
    assert "ALICE@CORP.LOCAL" in result.stdout


def test_entity_ambiguous_exits_2() -> None:
    result = _invoke("--mock", "entity", "ALICE")
    assert result.exit_code == 2


def test_hunt_hybrid_dry_run_emits_cypher() -> None:
    result = _invoke("--mock", "hunt", "hybrid", "ALICE@CORP.LOCAL", "--dry-run")
    assert result.exit_code == 0
    assert "STARTS WITH 'AZ'" in result.stdout
    assert "ALICE@CORP.LOCAL" in result.stdout


def test_hunt_tier_zero_dry_run_emits_cypher() -> None:
    result = _invoke("--mock", "hunt", "tier-zero", "ALICE@CORP.LOCAL", "--dry-run")
    assert result.exit_code == 0
    assert "admin_tier_0" in result.stdout


def test_triage_ranks_domains_by_exposure() -> None:
    result = _invoke("--mock", "--json", "triage")
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert rows  # at least one domain
    assert "domain" in rows[0] and "exposure" in rows[0]
    # Sorted by exposure descending.
    exposures = [r["exposure"] for r in rows if isinstance(r["exposure"], (int, float))]
    assert exposures == sorted(exposures, reverse=True)


def test_posture_latest_per_domain() -> None:
    result = _invoke("--mock", "--json", "posture")
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert rows and "exposure" in rows[0]


def test_choke_tier_zero_ranks_helpdesk() -> None:
    result = _invoke("--mock", "--json", "choke", "--tier-zero")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["exposed_sources"] == 5
    top = payload["choke_points"][0]
    assert "HELPDESK" in top["name"]
    assert top["principals_cut"] == 3


def test_choke_requires_a_seed() -> None:
    result = _invoke("--mock", "choke")  # no target, no --tier-zero
    assert result.exit_code == 2


def test_leaks_finds_cross_domain() -> None:
    result = _invoke("--mock", "--json", "leaks", "--tier-zero")
    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    # The synthetic graph has DEV-ADMIN@DEV -> CORP Domain Admins (a leak).
    leak = next(r for r in rows if r["from_domain"].startswith("DEV"))
    assert leak["to_domain"] == "CORP.LOCAL"
    assert leak["into_tier_zero"] == "yes"


def test_map_mermaid_and_dot() -> None:
    m = _invoke("--mock", "map", "--tier-zero")
    assert m.exit_code == 0
    assert m.stdout.startswith("graph RL")
    assert "DOMAIN ADMINS@CORP.LOCAL" in m.stdout
    assert "HELPDESK@CORP.LOCAL" in m.stdout

    d = _invoke("--mock", "map", "--tier-zero", "--format", "dot")
    assert d.exit_code == 0
    assert d.stdout.startswith("digraph")


def test_info_shows_paths_and_version() -> None:
    result = _invoke("--mock", "--json", "info")
    assert result.exit_code == 0
    details = json.loads(result.stdout)
    assert details["bhe version"]
    assert "snapshots" in details["cache dir"]


def test_cache_list_and_clear(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("BHE_CACHE_DIR", str(tmp_path))
    (tmp_path / "abc123.json").write_text("{}", encoding="utf-8")

    listed = _invoke("--mock", "--json", "cache", "list")
    assert listed.exit_code == 0
    rows = json.loads(listed.stdout)
    assert any(r["file"] == "abc123.json" for r in rows)

    cleared = _invoke("--mock", "cache", "clear")
    assert cleared.exit_code == 0
    assert "Cleared 1" in cleared.stdout
    assert not list(tmp_path.glob("*.json"))


def test_choke_concurrency_flag_accepted() -> None:
    result = _invoke("--mock", "--json", "choke", "--tier-zero", "--concurrency", "2")
    assert result.exit_code == 0
    assert json.loads(result.stdout)["exposed_sources"] == 5


def test_doctor_runs() -> None:
    # Doctor returns FAIL against fixtures (no live tenant) -> exit 1, but it
    # must run cleanly and print a report.
    result = _invoke("--mock", "doctor")
    assert result.exit_code in (0, 1)
    assert "Overall" in result.stdout

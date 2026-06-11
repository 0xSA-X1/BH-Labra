"""Tests for the on-disk snapshot cache (round-trip, TTL, keying, isolation)."""

from __future__ import annotations

import pytest

from bhe import cache
from bhe.scope import Edge, Node, Snapshot


@pytest.fixture(autouse=True)
def _isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("BHE_CACHE_DIR", str(tmp_path))
    return tmp_path


def _snap() -> Snapshot:
    nodes = {
        "DA": Node("DA", "DOMAIN ADMINS", "Group", 0, domain="CORP.LOCAL"),
        "HELP": Node("HELP", "HELPDESK", "Group", 1, domain="CORP.LOCAL", fan_in=2),
        "AU": Node("AU", "AUTH USERS", "Group", 2, domain="CORP.LOCAL", fan_in=9000, truncated=True),
    }
    edges = [
        Edge("HELP", "HELPDESK", "Group", "GenericAll", "DA", "CORP.LOCAL"),
        Edge("AU", "AUTH USERS", "Group", "MemberOf", "HELP", "CORP.LOCAL"),
    ]
    return Snapshot({"DA"}, nodes, edges, rounds=2, truncated=["AU"], hit_limit=False)


def test_round_trip_preserves_everything() -> None:
    key = cache.snapshot_key("acme", None, True, 4, 50)
    cache.save_snapshot(key, _snap())
    loaded = cache.load_snapshot(key)
    assert loaded is not None
    snap, age = loaded
    assert age >= 0
    assert snap.seeds == {"DA"}
    assert snap.nodes["DA"].domain == "CORP.LOCAL"
    assert snap.nodes["AU"].truncated and snap.nodes["AU"].fan_in == 9000
    assert len(snap.edges) == 2
    assert snap.edges[0].source_domain == "CORP.LOCAL"
    assert snap.truncated == ["AU"]


def test_ttl_expiry_is_a_miss() -> None:
    key = cache.snapshot_key("acme", None, True, 4, 50)
    cache.save_snapshot(key, _snap())
    assert cache.load_snapshot(key, ttl=-1) is None  # forced stale


def test_missing_key_is_a_miss() -> None:
    assert cache.load_snapshot("never|written|4|50") is None


def test_keys_are_param_sensitive() -> None:
    base = cache.snapshot_key("acme", None, True, 4, 50)
    assert base == cache.snapshot_key("acme", None, True, 4, 50)
    assert base != cache.snapshot_key("acme", "ALICE", False, 4, 50)
    assert base != cache.snapshot_key("acme", None, True, 3, 50)
    assert base != cache.snapshot_key("other", None, True, 4, 50)


def test_corrupt_cache_is_a_miss(_isolate_cache) -> None:
    key = cache.snapshot_key("acme", None, True, 4, 50)
    cache.save_snapshot(key, _snap())
    # Corrupt the file -> load must not raise, just miss.
    next(_isolate_cache.iterdir()).write_text("{not json", encoding="utf-8")
    assert cache.load_snapshot(key) is None

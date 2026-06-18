"""The guided-Cypher builders must be correct AND read-only-safe."""

from __future__ import annotations

from bhe.api.readonly import assert_cypher_readonly
from bhe.queries import library


def test_hybrid_builder_targets_azure_labels() -> None:
    q = library.hybrid_path_to_azure("ALICE@CORP.LOCAL")  # azure-only convenience
    assert "shortestPath" in q
    assert "'AZ'" in q and "STARTS WITH pfx" in q
    assert "ALICE@CORP.LOCAL" in q
    assert_cypher_readonly(q)  # must not raise


def test_cross_platform_path_covers_opengraph() -> None:
    q = library.cross_platform_path("S-1-oid", library.DEFAULT_HYBRID_PREFIXES)
    # Default hybrid spans Azure AND OpenGraph platforms (Okta/GitHub/Jamf).
    for prefix in ("AZ", "Okta", "GitHub", "Jamf"):
        assert f"'{prefix}'" in q
    assert "s.objectid = 'S-1-oid'" in q
    assert_cypher_readonly(q)


def test_tier_zero_builder_uses_tags_and_all_paths() -> None:
    q = library.path_to_tier_zero("ALICE@CORP.LOCAL", all_paths=True)
    assert "allShortestPaths" in q
    assert "admin_tier_0" in q
    assert_cypher_readonly(q)


def test_builders_are_all_readonly_safe() -> None:
    # Every generated recipe must survive the write-clause guard.
    for q in (
        library.shortest_path("a", "b"),
        library.all_shortest_paths("a", "b"),
        library.path_to_tier_zero("a"),
        library.hybrid_path_to_azure("a"),
        library.kerberoastable_users("CORP.LOCAL"),
        library.asrep_roastable_users("CORP.LOCAL"),
    ):
        assert_cypher_readonly(q)


def test_path_builders_match_by_objectid() -> None:
    # hunt resolves names to objectids first, so the builders match by objectid.
    q = library.path_to_tier_zero("S-1-5-21-1-2-3-1119")
    assert "s.objectid = 'S-1-5-21-1-2-3-1119'" in q
    q2 = library.shortest_path("S-1-oid-a", "S-1-oid-b")
    assert "s.objectid = 'S-1-oid-a'" in q2
    assert "t.objectid = 'S-1-oid-b'" in q2


def test_escape_neutralises_quote_injection() -> None:
    # A name containing a quote must be escaped, not break out of the literal.
    q = library.shortest_path("a' RETURN n; //", "b")
    assert "\\'" in q
    assert_cypher_readonly(q)

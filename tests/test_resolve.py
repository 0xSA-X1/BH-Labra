"""Tests for name -> object resolution (the no-opaque-IDs UX)."""

from __future__ import annotations

import pytest

from bhe.api.client import BHEClient
from bhe.cli._resolve import (
    ResolutionError,
    looks_like_id,
    resolve_domain,
    resolve_principal,
)


def test_looks_like_id() -> None:
    assert looks_like_id("S-1-5-21-1111111111-2222222222-3333333333-1105")
    assert looks_like_id("11112222-3333-4444-5555-666677778888")
    assert not looks_like_id("ALICE@CORP.LOCAL")
    assert not looks_like_id("CORP.LOCAL")


async def _client() -> BHEClient:
    return BHEClient.connect("https://mock", "id", "key", mock=True)


async def test_resolve_domain_exact_name() -> None:
    async with await _client() as c:
        dom = await resolve_domain(c, "CORP.LOCAL")
    assert dom["id"] == "S-1-5-21-1111111111-2222222222-3333333333"


async def test_resolve_domain_by_id() -> None:
    async with await _client() as c:
        dom = await resolve_domain(c, "11112222-3333-4444-5555-666677778888")
    assert dom["name"] == "contoso.onmicrosoft.com"


async def test_resolve_domain_unique_substring() -> None:
    async with await _client() as c:
        dom = await resolve_domain(c, "contoso")
    assert dom["type"] == "azure"


async def test_resolve_domain_ambiguous_lists_candidates() -> None:
    async with await _client() as c:
        with pytest.raises(ResolutionError) as ei:
            await resolve_domain(c, "corp")  # CORP.LOCAL + DEV.CORP.LOCAL
    assert len(ei.value.candidates) == 2
    assert "ambiguous" in str(ei.value)


async def test_resolve_domain_none_lists_all() -> None:
    async with await _client() as c:
        with pytest.raises(ResolutionError) as ei:
            await resolve_domain(c, "nope.invalid")
    assert ei.value.candidates  # offers the full list to choose from


async def test_resolve_principal_id_passthrough() -> None:
    async with await _client() as c:
        node = await resolve_principal(c, "S-1-5-21-9-9-9-1234")
    assert node["objectid"].endswith("-1234")


async def test_resolve_principal_exact_name() -> None:
    async with await _client() as c:
        node = await resolve_principal(c, "ALICE@CORP.LOCAL")
    assert node["objectid"].endswith("-1105")


async def test_resolve_principal_ambiguous() -> None:
    async with await _client() as c:
        with pytest.raises(ResolutionError) as ei:
            await resolve_principal(c, "ALICE")  # no exact match among hits
    assert ei.value.candidates

"""Tests for the three-layer HMAC request signing."""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timezone

from bhe.api.hmac_auth import HMACAuth


def _manual_signature(
    key: str, method: str, uri: str, body: bytes, now: datetime
) -> str:
    """Independent re-implementation of the documented BloodHound chain."""
    op = hmac.new(key.encode(), (method + uri).encode(), hashlib.sha256).digest()
    datetime_str = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")[:13]
    dk = hmac.new(op, datetime_str.encode(), hashlib.sha256).digest()
    sig = hmac.new(dk, body, hashlib.sha256).digest()
    return base64.b64encode(sig).decode()


def test_signature_matches_documented_chain() -> None:
    auth = HMACAuth("token-id", "super-secret-key")
    now = datetime(2026, 6, 4, 12, 30, 0, tzinfo=timezone.utc)

    expected = _manual_signature("super-secret-key", "GET", "/api/v2/self", b"", now)
    assert auth._compute_signature("GET", "/api/v2/self", b"", now) == expected


def test_signature_includes_body() -> None:
    auth = HMACAuth("token-id", "k")
    now = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)
    body = b'{"query":"MATCH (n) RETURN n"}'

    with_body = auth._compute_signature("POST", "/api/v2/graphs/cypher", body, now)
    without_body = auth._compute_signature("POST", "/api/v2/graphs/cypher", b"", now)
    assert with_body != without_body
    assert with_body == _manual_signature("k", "POST", "/api/v2/graphs/cypher", body, now)


def test_signature_truncates_datetime_to_the_hour() -> None:
    auth = HMACAuth("id", "key")
    same_hour_a = datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc)
    same_hour_b = datetime(2026, 6, 4, 12, 59, 59, tzinfo=timezone.utc)
    next_hour = datetime(2026, 6, 4, 13, 0, 0, tzinfo=timezone.utc)

    sig_a = auth._compute_signature("GET", "/api/version", b"", same_hour_a)
    sig_b = auth._compute_signature("GET", "/api/version", b"", same_hour_b)
    sig_c = auth._compute_signature("GET", "/api/version", b"", next_hour)

    assert sig_a == sig_b  # within the same hour the signature is stable
    assert sig_a != sig_c  # a new hour changes the DateKey


def test_headers_shape() -> None:
    auth = HMACAuth("the-token-id", "key")
    headers = auth.sign_request("GET", "/api/v2/self")
    assert headers["Authorization"] == "bhesignature the-token-id"
    assert headers["Content-Type"] == "application/json"
    assert "Signature" in headers
    assert headers["RequestDate"].endswith("Z")

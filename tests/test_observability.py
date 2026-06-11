"""Unit tests for request observability: taxonomy, buffer, file mirror, wiring."""

from __future__ import annotations

import pytest

from bhe.api.client import BHEClient, BHEClientError
from bhe.api.readonly import ReadOnlyViolation
from bhe.observability import (
    Category,
    RequestLog,
    RequestRecord,
    classify,
    log_file_for,
    remediation_for,
)


def _rec(**kw) -> RequestRecord:
    base = dict(
        seq=1,
        correlation_id="000001",
        method="GET",
        endpoint="/api/v2/self",
        status=200,
        latency_ms=12.0,
        category=Category.OK,
    )
    base.update(kw)
    return RequestRecord(**base)


def test_classify_status_codes() -> None:
    assert classify(200) is Category.OK
    assert classify(204) is Category.OK
    assert classify(401) is Category.AUTH
    assert classify(403) is Category.AUTH
    assert classify(404) is Category.NOT_FOUND
    assert classify(429) is Category.RATE_LIMIT
    assert classify(400) is Category.BAD_REQUEST
    assert classify(422) is Category.BAD_REQUEST
    assert classify(418) is Category.BAD_REQUEST  # other 4xx
    assert classify(500) is Category.SERVER_ERROR
    assert classify(503) is Category.SERVER_ERROR


def test_classify_exceptions() -> None:
    assert classify(None, ReadOnlyViolation("nope")) is Category.BLOCKED
    assert classify(None, RuntimeError("boom")) is Category.NETWORK
    assert classify(None) is Category.NETWORK  # no status, no error


def test_remediation_present_for_failures_only() -> None:
    assert remediation_for(Category.OK) is None
    for cat in Category:
        if cat is Category.OK:
            continue
        assert remediation_for(cat), f"missing remediation for {cat}"


def test_record_as_line_and_props() -> None:
    ok = _rec()
    assert ok.ok is True
    assert ok.remediation is None
    line = ok.as_line()
    assert "000001" in line and "GET" in line and "200" in line

    err = _rec(status=404, category=Category.NOT_FOUND, retries=2, detail="missing")
    assert err.ok is False
    assert err.remediation
    eline = err.as_line()
    assert "retries=2" in eline and "missing" in eline


def test_request_log_buffer_caps_and_notifies() -> None:
    log = RequestLog(capacity=3)
    seen: list[str] = []
    unsub = log.subscribe(lambda r: seen.append(r.correlation_id))

    for i in range(5):
        log.add(_rec(seq=i, correlation_id=f"{i:06d}"))

    # Ring buffer keeps only the last 3, but every add notified the listener.
    assert [r.correlation_id for r in log.records()] == ["000002", "000003", "000004"]
    assert len(seen) == 5
    assert len(log) == 3

    unsub()
    log.add(_rec())
    assert len(seen) == 5  # no more notifications after unsubscribe


def test_request_log_listener_error_is_isolated() -> None:
    log = RequestLog()

    def boom(_r: RequestRecord) -> None:
        raise ValueError("listener blew up")

    log.subscribe(boom)
    log.add(_rec())  # must not raise
    assert len(log) == 1


def test_request_log_mirrors_to_file(tmp_path) -> None:
    log = RequestLog()
    path = tmp_path / "acme.log"
    log.attach_file(path)
    log.add(_rec(correlation_id="00ABCD", endpoint="/api/version"))

    contents = path.read_text(encoding="utf-8")
    assert "00ABCD" in contents
    assert "/api/version" in contents


def test_attach_file_swallows_bad_path(tmp_path) -> None:
    # Pointing the log "file" at an existing directory should fail to open but
    # never raise — observability must not take down the app.
    log = RequestLog()
    log.attach_file(tmp_path)  # tmp_path is a directory
    log.add(_rec())  # no exception
    assert len(log) == 1


def test_log_file_for_sanitises_name() -> None:
    p = log_file_for("acme/../prod corp")
    assert p.name == "acme_.._prod_corp.log"


async def test_client_records_ok_and_error() -> None:
    log = RequestLog()
    async with BHEClient.connect(
        "https://mock", "id", "key", mock=True, request_log=log
    ) as client:
        await client.get_available_domains()  # OK
        with pytest.raises(BHEClientError):
            await client._request("GET", "/api/v2/does-not-exist")  # 404

    records = log.records()
    assert len(records) == 2
    ok, missing = records
    assert ok.category is Category.OK and ok.status == 200
    assert missing.category is Category.NOT_FOUND and missing.status == 404
    # Correlation ids are sequential and endpoints have the query string stripped.
    assert ok.correlation_id == "000001"
    assert missing.endpoint == "/api/v2/does-not-exist"


async def test_client_records_blocked_request() -> None:
    log = RequestLog()
    async with BHEClient.connect(
        "https://mock", "id", "key", mock=True, request_log=log
    ) as client:
        with pytest.raises(ReadOnlyViolation):
            await client._request("DELETE", "/api/v2/clients/123")

    (record,) = log.records()
    assert record.category is Category.BLOCKED
    assert record.status is None

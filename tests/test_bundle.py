"""Tests for support-bundle assembly."""

from __future__ import annotations

import json
import zipfile

from bhe.bundle import write_bundle
from bhe.observability import Category, RequestLog, RequestRecord


def _log_with(n_ok: int, n_err: int) -> RequestLog:
    log = RequestLog()
    seq = 0
    for _ in range(n_ok):
        seq += 1
        log.add(
            RequestRecord(
                seq=seq,
                correlation_id=f"{seq:06d}",
                method="GET",
                endpoint="/api/version",
                status=200,
                latency_ms=5.0,
                category=Category.OK,
            )
        )
    for _ in range(n_err):
        seq += 1
        log.add(
            RequestRecord(
                seq=seq,
                correlation_id=f"{seq:06d}",
                method="GET",
                endpoint="/api/v2/missing",
                status=404,
                latency_ms=3.0,
                category=Category.NOT_FOUND,
            )
        )
    return log


def test_write_bundle_contents(tmp_path) -> None:
    out = tmp_path / "bundle.zip"
    history = tmp_path / "acme.log"
    history.write_text("historic line\n", encoding="utf-8")

    result = write_bundle(
        out,
        app_meta={"tool": "bh-scout", "version": "9.9.9"},
        profile_meta={"name": "acme", "token_key": "***"},
        doctor_json=json.dumps({"worst": "WARN", "checks": []}),
        session_log=_log_with(2, 1),
        history_files=[history, tmp_path / "does-not-exist.log"],
    )

    assert result == out and out.is_file()
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        assert {
            "meta.json",
            "profile.json",
            "doctor.json",
            "session.log",
            "README.txt",
            "history/acme.log",
        } <= names
        # The missing history file is skipped, not fatal.
        assert "history/does-not-exist.log" not in names

        meta = json.loads(zf.read("meta.json"))
        assert meta["version"] == "9.9.9"

        session = zf.read("session.log").decode()
        assert session.count("\n") == 3  # 3 records, one per line + trailing nl
        assert "/api/v2/missing" in session

        readme = zf.read("README.txt").decode()
        assert "Session requests: 3 (1 errors)" in readme
        assert "Doctor overall:   WARN" in readme

        # The redacted profile never carries a real secret.
        profile = json.loads(zf.read("profile.json"))
        assert profile["token_key"] == "***"


def test_write_bundle_creates_parent_dirs(tmp_path) -> None:
    out = tmp_path / "nested" / "deeper" / "bundle.zip"
    write_bundle(
        out,
        app_meta={},
        profile_meta={},
        doctor_json="{}",
        session_log=RequestLog(),
    )
    assert out.is_file()

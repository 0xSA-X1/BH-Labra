"""Support-bundle assembly for ``bhe bundle``.

A support bundle is a single ``.zip`` a consultant can attach to a ticket; it
packages everything needed to triage a tenant offline:

* ``meta.json``        - bhe version, platform, generation time
* ``profile.json``     - the connection profile, **redacted** (never the token key)
* ``doctor.json``      - a fresh diagnostics report (the Doctor battery as JSON)
* ``session.log``      - the request trace captured while building the bundle
* ``history/*.log``    - any persisted rotating request logs for the profile

The pure assembly (:func:`write_bundle`) is split from the I/O that produces its
inputs (connecting + running diagnostics) so it can be unit-tested directly.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Iterable

from bhe.observability import RequestLog


def write_bundle(
    out_path: Path,
    *,
    app_meta: dict,
    profile_meta: dict,
    doctor_json: str,
    session_log: RequestLog,
    history_files: Iterable[Path] = (),
) -> Path:
    """Write a support bundle zip to ``out_path`` and return the path.

    Args:
        out_path: Destination ``.zip`` path (parent dirs are created).
        app_meta: Version / platform / timestamp metadata.
        profile_meta: Redacted profile fields (must not contain secrets).
        doctor_json: Diagnostics report rendered as JSON.
        session_log: The request log captured during bundle generation; its
            buffered records are written one per line as ``session.log``.
        history_files: Pre-existing rotating log files to include under
            ``history/`` (missing files are skipped).
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    session_lines = "\n".join(rec.as_line() for rec in session_log.records())
    summary = _summarise(session_log, doctor_json)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta.json", json.dumps(app_meta, indent=2, sort_keys=True))
        zf.writestr("profile.json", json.dumps(profile_meta, indent=2, sort_keys=True))
        zf.writestr("doctor.json", doctor_json)
        zf.writestr("session.log", session_lines + ("\n" if session_lines else ""))
        zf.writestr("README.txt", summary)
        for path in history_files:
            try:
                if path.is_file():
                    zf.write(path, arcname=f"history/{path.name}")
            except OSError:
                continue  # unreadable rotated log - skip rather than fail the bundle
    return out_path


def _summarise(session_log: RequestLog, doctor_json: str) -> str:
    """Build the human-readable README placed at the root of the bundle."""
    records = session_log.records()
    errors = sum(1 for r in records if not r.ok)
    try:
        worst = json.loads(doctor_json).get("worst", "?")
    except (ValueError, AttributeError):
        worst = "?"
    return (
        "bhe support bundle\n"
        "=======================\n\n"
        "Contents:\n"
        "  meta.json     bhe version / platform / generation time\n"
        "  profile.json  connection profile (redacted; no token key)\n"
        "  doctor.json   tenant health diagnostics report\n"
        "  session.log   request trace captured while generating this bundle\n"
        "  history/      persisted per-tenant request logs, if any\n\n"
        f"Session requests: {len(records)} ({errors} errors)\n"
        f"Doctor overall:   {worst}\n"
    )

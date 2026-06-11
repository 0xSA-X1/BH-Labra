"""Request-level observability: structured records, error taxonomy, log files.

bhe is a *triage* tool, so every API call a support engineer makes should
be inspectable after the fact.  This module is UI-agnostic (mirroring the split
between :mod:`bhe.diagnostics` and its Doctor tab) and provides three things:

1. :class:`RequestRecord` - one structured row per API request (method, endpoint,
   status, latency, correlation id, error category + remediation).
2. :class:`RequestLog` - an in-memory ring buffer the TUI Logs pane renders live,
   which also mirrors every record to a rotating per-tenant file for the support
   bundle export.
3. :func:`classify` - the error taxonomy: maps an HTTP status / exception onto a
   human category and a one-line remediation hint.

Nothing here imports Textual or the API client, so it stays cheap to unit-test.
"""

from __future__ import annotations

import logging
import logging.handlers
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Deque, Iterable

logger = logging.getLogger("bhe")

# Where rotating per-tenant logs live.  We keep this beside other app state and
# resolve it lazily so importing the module never touches the filesystem.
_LOG_SUBDIR = "logs"


class Category(str, Enum):
    """Coarse outcome bucket for an API request - drives colour + remediation."""

    OK = "OK"
    AUTH = "AUTH"
    NOT_FOUND = "NOT_FOUND"
    RATE_LIMIT = "RATE_LIMIT"
    BAD_REQUEST = "BAD_REQUEST"
    SERVER_ERROR = "SERVER_ERROR"
    NETWORK = "NETWORK"
    BLOCKED = "BLOCKED"


# One remediation hint per non-OK category.  Phrased as the next thing a support
# engineer should check, not a restatement of the error.
_REMEDIATION: dict[Category, str] = {
    Category.AUTH: (
        "Token rejected. Verify the token id/key and that its BHE role has "
        "read access; re-create the API token if it was rotated."
    ),
    Category.NOT_FOUND: (
        "Endpoint or resource not found - often an API path unsupported on this "
        "BHE version. Check the tenant version on the Doctor tab."
    ),
    Category.RATE_LIMIT: (
        "Rate limited (429). bhe backs off and retries automatically; if it "
        "persists, reduce concurrent tabs/refreshes."
    ),
    Category.BAD_REQUEST: (
        "Request rejected as malformed. Check query parameters / Cypher syntax."
    ),
    Category.SERVER_ERROR: (
        "BHE server error (5xx). Usually transient - retry shortly; if sustained, "
        "check tenant health / the BHE status page."
    ),
    Category.NETWORK: (
        "Could not reach the tenant. Check the base URL, DNS, proxy and TLS "
        "(corporate MITM proxies often need their CA trusted)."
    ),
    Category.BLOCKED: (
        "Blocked by the read-only guard - this is by design; the request would "
        "have mutated the tenant."
    ),
}


def classify(status_code: int | None, error: BaseException | None = None) -> Category:
    """Map an HTTP status (and/or exception) onto an outcome :class:`Category`.

    Args:
        status_code: The response status, or ``None`` if the request never got a
            response (network failure, or blocked before sending).
        error: The exception raised, if any. A :class:`ReadOnlyViolation` maps to
            :attr:`Category.BLOCKED`; any other pre-response error to ``NETWORK``.

    Returns:
        The single best-fit category.
    """
    if error is not None and status_code is None:
        # Imported lazily to avoid a circular import with the api package.
        from bhe.api.readonly import ReadOnlyViolation

        if isinstance(error, ReadOnlyViolation):
            return Category.BLOCKED
        return Category.NETWORK

    if status_code is None:
        return Category.NETWORK
    if status_code < 400:
        return Category.OK
    if status_code in (401, 403):
        return Category.AUTH
    if status_code == 404:
        return Category.NOT_FOUND
    if status_code == 429:
        return Category.RATE_LIMIT
    if status_code in (400, 422):
        return Category.BAD_REQUEST
    if status_code >= 500:
        return Category.SERVER_ERROR
    return Category.BAD_REQUEST  # any other 4xx


def remediation_for(category: Category) -> str | None:
    """Return the remediation hint for a category (``None`` for :attr:`Category.OK`)."""
    return _REMEDIATION.get(category)


@dataclass(slots=True)
class RequestRecord:
    """One API request's structured outcome, as shown in the Logs pane / bundle."""

    seq: int
    correlation_id: str
    method: str
    endpoint: str
    status: int | None
    latency_ms: float
    category: Category
    retries: int = 0
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.category is Category.OK

    @property
    def remediation(self) -> str | None:
        return remediation_for(self.category)

    def as_line(self) -> str:
        """Single-line, log-file / bundle representation (no timestamp - the file
        handler prefixes one)."""
        status = self.status if self.status is not None else "---"
        retry = f" retries={self.retries}" if self.retries else ""
        detail = f" :: {self.detail}" if self.detail else ""
        return (
            f"[{self.correlation_id}] {self.method:4} {self.endpoint} "
            f"-> {status} {self.category.value} {self.latency_ms:.0f}ms{retry}{detail}"
        )


class RequestLog:
    """In-memory ring buffer of :class:`RequestRecord`, mirrored to a file.

    The TUI Logs pane reads :meth:`records` to render and registers a listener via
    :meth:`subscribe` for live appends.  A rotating file handler (when configured)
    persists every record so ``bhe bundle`` can ship it with a ticket.
    """

    def __init__(self, capacity: int = 500) -> None:
        self._records: Deque[RequestRecord] = deque(maxlen=capacity)
        self._listeners: list[Callable[[RequestRecord], None]] = []
        self._seq = 0
        self._file_logger: logging.Logger | None = None

    # -- writing -------------------------------------------------------------

    def next_seq(self) -> int:
        """Allocate the next monotonic sequence number (also the correlation seed)."""
        self._seq += 1
        return self._seq

    def add(self, record: RequestRecord) -> None:
        """Append a record, mirror it to the file log, and notify listeners."""
        self._records.append(record)
        if self._file_logger is not None:
            level = logging.INFO if record.ok else logging.WARNING
            self._file_logger.log(level, record.as_line())
        for listener in list(self._listeners):
            try:
                listener(record)
            except Exception:  # noqa: BLE001 - a bad listener must not break logging
                logger.debug("RequestLog listener raised", exc_info=True)

    # -- reading -------------------------------------------------------------

    def records(self) -> list[RequestRecord]:
        """Snapshot of buffered records, oldest first."""
        return list(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def subscribe(self, listener: Callable[[RequestRecord], None]) -> Callable[[], None]:
        """Register a live-append listener; returns an unsubscribe callable."""
        self._listeners.append(listener)

        def _unsub() -> None:
            try:
                self._listeners.remove(listener)
            except ValueError:
                pass

        return _unsub

    # -- file mirroring ------------------------------------------------------

    def attach_file(self, path: Path, *, max_bytes: int = 1_000_000, backups: int = 3) -> None:
        """Mirror records to a rotating file at ``path`` (best-effort).

        A failure to open the file (e.g. Windows Controlled Folder Access) is
        logged and swallowed - observability must never take down the app.
        """
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = logging.handlers.RotatingFileHandler(
                path, maxBytes=max_bytes, backupCount=backups, encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            file_logger = logging.getLogger(f"bhe.requests.{path.stem}")
            file_logger.setLevel(logging.INFO)
            file_logger.propagate = False
            # Reconfiguring the same profile twice shouldn't double-write.
            for existing in list(file_logger.handlers):
                file_logger.removeHandler(existing)
                existing.close()
            file_logger.addHandler(handler)
            self._file_logger = file_logger
        except OSError as exc:
            logger.warning("Could not open request log %s: %s", path, exc)
            self._file_logger = None


def app_log_dir() -> Path:
    """Return the per-user directory for bhe logs (created on demand).

    macOS -> ``~/Library/Logs/bhe``; Windows -> ``%LOCALAPPDATA%\\bhe\\logs``;
    otherwise ``$XDG_DATA_HOME/bhe/logs`` or ``~/.local/share/bhe/logs``.
    """
    import os
    import sys

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "bhe"
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "bhe" / _LOG_SUBDIR


def log_file_for(profile_name: str) -> Path:
    """Path of the rotating request log for a given profile (sanitised name)."""
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in profile_name) or "default"
    return app_log_dir() / f"{safe}.log"


def iter_log_files(profile_name: str) -> Iterable[Path]:
    """Yield the request log and its rotated backups for a profile, if present."""
    primary = log_file_for(profile_name)
    if primary.exists():
        yield primary
    for i in range(1, 10):
        backup = primary.with_name(f"{primary.name}.{i}")
        if backup.exists():
            yield backup

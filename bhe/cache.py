"""On-disk snapshot cache so repeated choke/leaks/map on a tenant are instant.

Backward scoping is the expensive step (many bounded queries).  Since a
consultant runs ``choke`` then ``leaks`` then ``map`` over the *same* seeds, we
persist the :class:`~bhe.scope.Snapshot` keyed by (profile, seed-spec, depth,
fan-in) and reuse it within a TTL.  BHE data changes nightly, so a short TTL
(default 1h) keeps it fresh; ``--refresh`` always rebuilds.

Cache I/O is best-effort: any failure falls back to recomputing, never crashes.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path

from bhe.scope import Edge, Node, Snapshot

DEFAULT_TTL_SECONDS = 3600


def app_cache_dir() -> Path:
    """Per-user cache dir (``BHE_CACHE_DIR`` overrides — used to isolate tests).

    macOS -> ``~/Library/Caches/bhe/snapshots``; Windows ->
    ``%LOCALAPPDATA%\\bhe\\snapshots``; otherwise ``$XDG_CACHE_HOME/bhe/snapshots``.
    """
    override = os.environ.get("BHE_CACHE_DIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "bhe" / "snapshots"
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        xdg = os.environ.get("XDG_CACHE_HOME")
        base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "bhe" / "snapshots"


def snapshot_key(
    profile: str,
    target: str | None,
    tier_zero: bool,
    max_depth: int,
    fanin: int,
    domain: str | None = None,
) -> str:
    """Stable cache key for a scoping run.

    ``domain`` is folded in so a domain-scoped snapshot never collides with the
    full-estate one for the same profile/depth/fan-in.
    """
    seed = "tier-zero" if tier_zero else (target or "")
    if domain:
        seed = f"{seed}@{domain.strip().lower()}"
    return f"{profile}|{seed}|{max_depth}|{fanin}"


def _key_path(key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return app_cache_dir() / f"{digest}.json"


def _to_dict(snapshot: Snapshot) -> dict:
    return {
        "seeds": sorted(snapshot.seeds),
        "nodes": [asdict(n) for n in snapshot.nodes.values()],
        "edges": [asdict(e) for e in snapshot.edges],
        "rounds": snapshot.rounds,
        "truncated": list(snapshot.truncated),
        "hit_limit": snapshot.hit_limit,
    }


def _from_dict(data: dict) -> Snapshot:
    nodes = {n["objectid"]: Node(**n) for n in data["nodes"]}
    edges = [Edge(**e) for e in data["edges"]]
    return Snapshot(
        seeds=set(data["seeds"]),
        nodes=nodes,
        edges=edges,
        rounds=data.get("rounds", 0),
        truncated=list(data.get("truncated", [])),
        hit_limit=data.get("hit_limit", False),
    )


def save_snapshot(key: str, snapshot: Snapshot) -> None:
    """Persist a snapshot (best-effort; swallows I/O errors)."""
    try:
        path = _key_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_to_dict(snapshot)), encoding="utf-8")
    except OSError:
        pass


def load_snapshot(key: str, *, ttl: float = DEFAULT_TTL_SECONDS) -> tuple[Snapshot, float] | None:
    """Return ``(snapshot, age_seconds)`` if a fresh cache exists, else ``None``."""
    path = _key_path(key)
    try:
        if not path.is_file():
            return None
        age = time.time() - path.stat().st_mtime
        if age > ttl:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return _from_dict(data), age
    except (OSError, ValueError, KeyError, TypeError):
        return None  # corrupt/old-format cache -> treat as a miss

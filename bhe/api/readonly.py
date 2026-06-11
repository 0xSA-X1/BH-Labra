"""Read-only enforcement for the BHE client.

This is the core safety contract of bhe: nothing it does may mutate a
customer's BloodHound Enterprise tenant.  Enforcement is layered:

1. :func:`assert_request_allowed` - a transport allowlist applied to *every*
   request before it is signed/sent.  Only ``GET`` is permitted, plus the
   single read-only ``POST /api/v2/graphs/cypher`` Cypher endpoint.  Any other
   method, or a POST to any other path, raises :class:`ReadOnlyViolation`.
2. :func:`assert_cypher_readonly` - rejects Cypher containing write clauses so a
   read-only API role isn't even relied upon for the Cypher console.

These are belt-and-suspenders on top of the operational guidance to provision
the API token against a **Read-Only** BHE role.
"""

from __future__ import annotations

import re

# The only non-GET request bhe ever issues: the Cypher *read* endpoint, which
# is a POST purely because the query travels in the body.
CYPHER_ENDPOINT = "/api/v2/graphs/cypher"

# Mutating Cypher clauses, matched as whole words (case-insensitive).  Any of
# these turns a query into a write, so we refuse to send it.
_MUTATING_CLAUSES = (
    "CREATE",
    "MERGE",
    "DELETE",
    "DETACH",
    "SET",
    "REMOVE",
    "FOREACH",
    "DROP",
    "LOAD",  # LOAD CSV
)
_MUTATING_RE = re.compile(
    r"(?<![\w.])(" + "|".join(_MUTATING_CLAUSES) + r")(?![\w.])",
    re.IGNORECASE,
)

# Procedure calls that can write.  BHE's Cypher surface does not need CALL at
# all for the read use cases we ship, so we block known write-capable namespaces
# rather than try to enumerate safe ones.
_WRITE_CALL_RE = re.compile(
    r"(?<![\w.])CALL\s+(apoc\.(?:create|merge|refactor|nodes\.delete|"
    r"periodic)|db\.create|db\.index)",
    re.IGNORECASE,
)

# Line/block comments are stripped before scanning so a write clause can't hide
# behind `// CREATE` framing that some parsers still execute downstream.
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


class ReadOnlyViolation(Exception):
    """Raised when an operation would (or could) mutate the tenant.

    Caught and surfaced as a friendly message in the TUI rather than crashing -
    it signals a programming/usage error, not a transient failure.
    """


def assert_request_allowed(method: str, endpoint: str) -> None:
    """Gate an outgoing request through the read-only allowlist.

    Args:
        method: HTTP method (case-insensitive).
        endpoint: Request path beginning with ``/`` (query string excluded).

    Raises:
        ReadOnlyViolation: If the method/endpoint pair is not read-only.
    """
    verb = method.upper()
    path = endpoint.split("?", 1)[0]

    if verb == "GET":
        return
    if verb == "POST" and path == CYPHER_ENDPOINT:
        return

    raise ReadOnlyViolation(
        f"Blocked non-read-only request: {verb} {path}. bhe only permits GET "
        f"and POST {CYPHER_ENDPOINT}."
    )


def _strip_comments(query: str) -> str:
    """Remove Cypher line/block comments prior to keyword scanning."""
    without_block = _BLOCK_COMMENT_RE.sub(" ", query)
    return _LINE_COMMENT_RE.sub(" ", without_block)


def assert_cypher_readonly(query: str) -> None:
    """Reject a Cypher query that contains write clauses.

    Args:
        query: The raw Cypher string the user wants to run.

    Raises:
        ReadOnlyViolation: If a mutating clause or write procedure is present.
    """
    scannable = _strip_comments(query)

    match = _MUTATING_RE.search(scannable)
    if match:
        raise ReadOnlyViolation(
            f"Blocked write Cypher: '{match.group(1).upper()}' clause is not "
            f"allowed in read-only mode."
        )

    call_match = _WRITE_CALL_RE.search(scannable)
    if call_match:
        raise ReadOnlyViolation(
            f"Blocked write procedure: CALL {call_match.group(1)} is not allowed "
            f"in read-only mode."
        )

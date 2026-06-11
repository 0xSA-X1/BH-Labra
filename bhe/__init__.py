"""bhe - an ergonomic, read-only CLI for the BloodHound Enterprise API.

``bhe`` is a Python command-line wrapper over the documented BloodHound
Enterprise ``/api/v2`` REST API, using HMAC-signed requests.  It pairs curated
verbs (``bhe jobs``, ``bhe domains``, ``bhe cypher`` ...) with a generic
``bhe get <path>`` escape hatch so any endpoint is reachable immediately.

Every code path is read-only by construction: the transport refuses any HTTP
method/endpoint that could mutate tenant state, and the Cypher path rejects
write clauses.
"""

from __future__ import annotations

__version__ = "0.1.0"

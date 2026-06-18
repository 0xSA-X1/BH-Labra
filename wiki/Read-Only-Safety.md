# Read-Only Safety

`bhe` is **read-only by construction**, not just by convention. You can point it
at a customer's production BloodHound Enterprise tenant without any risk of
changing its state. Here's exactly how that's guaranteed.

## Two enforced layers

**1. Transport allowlist** — applied to *every* request before it is signed or
sent. Only two things are permitted:

- any `GET`, and
- the single read-only `POST /api/v2/graphs/cypher` (Cypher travels in the body,
  so it must be a POST).

Anything else — a `PUT`, `PATCH`, `DELETE`, or a `POST` to any other path —
raises a `ReadOnlyViolation` **before** the request is built. There is no code
path that issues a mutating request.

**2. Cypher write-clause guard** — even the one allowed POST is screened. Any
query containing a write clause (`CREATE`, `MERGE`, `SET`, `DELETE`, `DETACH`,
`REMOVE`, `FOREACH`, `DROP`, `LOAD CSV`) or a write-capable `CALL` procedure is
rejected before sending — comments are stripped first so nothing can hide behind
`// CREATE`.

```console
$ bhe cypher "MATCH (n) DETACH DELETE n"
Blocked write Cypher: 'DELETE' clause is not allowed in read-only mode.
```

> 📸 **Screenshot:** the blocked-write example above.

## Token handling

- A **Read-Only** API role is all `bhe` needs (and all you should grant it).
- The Token Key is used only to compute the request signature **locally** — it is
  never sent over the wire.
- It is stored in your OS keyring (macOS Keychain / Windows Credential Manager),
  never in plaintext config. See [Configuration](Configuration).

## What this means in practice

- Safe to run against production during an engagement.
- Safe to script and schedule — ambiguous inputs stop with candidates rather than
  guessing, so a typo can't select the wrong object.
- The generic `bhe get <path>` escape hatch is still GET-only, so it can't be used
  to reach a mutating endpoint.

> Note: audited "safe-action" writes may arrive later as an explicit, clearly
> labeled opt-in. The current version is strictly read-only.

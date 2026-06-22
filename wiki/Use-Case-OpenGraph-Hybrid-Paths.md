# Use Case: OpenGraph & Hybrid Paths

**Scenario:** the tenant has BloodHound **OpenGraph** data — Okta, GitHub, and/or
Jamf (Mac) — alongside Active Directory and Entra/Azure. Can you see cross-platform
("hybrid") attack paths?

**Yes.** OpenGraph nodes live in the same graph as AD/Azure (their kinds are
platform-prefixed, e.g. Azure `AZ*`, Okta `Okta_*`), so everything graph-native in
`bhe` traverses them automatically.

## What already sees OpenGraph (no special flags)

- **`cypher`** — query any node kind directly.
- **`choke` / `leaks`** — platform-agnostic; they walk *any* edge into Tier Zero,
  so OpenGraph nodes/edges on a path to a tagged crown jewel show up.
- **`search` / `entity`** — find and inspect OpenGraph objects.

> ℹ️ For OpenGraph crown jewels to seed the backward engine, they must be **tagged
> Tier Zero** in BHE (like any other crown jewel).

## Hybrid pathfinding — `hunt hybrid`

```console
$ bhe hunt hybrid alice@corp.local             # AD -> Azure + Okta + GitHub + Jamf
$ bhe hunt hybrid alice@corp.local --to okta   # scope to one platform
$ bhe hunt hybrid alice@corp.local --to github
$ bhe hunt hybrid alice@corp.local --dry-run   # see the generated Cypher
```

By default `hunt hybrid` matches a target on **any** non-AD platform (Azure,
Okta, GitHub, Jamf). `--to` scopes to one platform — or pass a **raw node-label
prefix** for a custom OpenGraph source.

> 📸 **Screenshot:** `bhe hunt hybrid alice@corp.local --dry-run`

## Leakage labels platforms

`bhe leaks` recognizes platform-prefixed nodes, so a crossing reads as its
platform ("Okta", "Azure", "GitHub", "Jamf") rather than a blank/unknown domain:

```console
$ bhe leaks --tier-zero
```

## Confirm your tenant's OpenGraph kinds

Collectors can define custom kinds, so confirm what yours look like:

```console
$ bhe cypher "MATCH (n) RETURN DISTINCT labels(n) LIMIT 100"
```

If a platform's kinds don't start with the expected prefix (`Okta`, `GitHub`,
`Jamf`, `AZ`), use `bhe hunt hybrid <user> --to <actual-prefix>` to target it
exactly.

## In an OpenGraph estate

Nothing about the [triage workflow](Use-Case-New-Customer-Triage) changes —
`tier-zero`, `choke`, and `leaks` already span every platform (that's the point of
the section above). The only OpenGraph-specific move is **`bhe hunt hybrid <user>`**
for explicit cross-platform paths, plus the platform labels `leaks` shows.

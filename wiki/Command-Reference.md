# Command Reference

Every command, grouped by area. Examples use `--mock` so you can run them as-is;
swap for `--profile <tenant>` against a real tenant.

**Global flags** (before the command): `--profile/-p <name>`, `--mock`,
`--profiles-file <path>`, `--json/-j`, `--version`. Add `--help` to any command
for its flags. Output is Rich tables / key-value by default; `--json` emits raw
JSON (and `bhe get` is always JSON).

**Selectors** accept a **name or an id** everywhere. Ambiguous names print the
candidates and exit non-zero (never a guess), so commands stay scriptable.

---

## Identity & host

### `bhe self`
Authenticated identity and token role. `bhe --mock self`

### `bhe api-version`
The tenant's BHE API / server version. `bhe --mock api-version`

### `bhe profile`
The active connection profile, redacted (never the Token Key).

### `bhe info`
Version, active profile, and where logs/cache live on this host.

### `bhe doctor [--strict]`
Read-only tenant health battery; exits non-zero on failures (`--strict` also on
warnings). `bhe --mock doctor`

### `bhe bundle [--out file.zip]`
Writes a support bundle (diagnostics + request trace) for a ticket.

---

## Posture, exposure & "where to start"

### `bhe posture [domain] [--explain] [--history]`
Risk-posture per domain, ranked by **exposure %** (BHE's exposure index ×100).
Pass a domain to scope to one; `--history` shows every snapshot, not just the
latest.
```console
$ bhe --mock posture
$ bhe --mock posture CORP.LOCAL
```

### `bhe posture <domain> --explain`
**Breaks down what drives a domain's exposure** — the headline number plus the
findings behind it (severity, affected principals, per-finding exposure % and
impact %). See [Exposure Deep-Dive](Exposure-Deep-Dive).
```console
$ bhe --mock posture CORP.LOCAL --explain
```

### `bhe triage [--domain/-d <domain>] [--top/-n N] [--by-type]`
Ranks attack-path **findings across the whole estate** (severity × affected
principals × (1 + impact)) — the cross-domain "fix these first" list the per-domain
UI can't give you. `--domain` scopes to one; `--by-type` rolls up to one row per
finding type.
```console
$ bhe --mock triage
$ bhe --mock triage --by-type
$ bhe --mock triage -d CORP.LOCAL
```

### `bhe tier-zero [domain]`
Lists the Tier Zero / high-value principals (the crown jewels the engine funnels
into). `bhe --mock tier-zero`

### `bhe findings <domain>`
A domain's attack-path findings, counted per finding type.
`bhe --mock findings CORP.LOCAL`

### `bhe quality [domain]`
**Collection completeness / data quality — is the data trustworthy?** No domain =
estate completeness (% of sessions & local-admins collected); with a domain = its
latest collection counts + completeness. Run this *before* trusting findings.
```console
$ bhe --mock quality
$ bhe --mock quality CORP.LOCAL
```

---

## Backward attack-path engine

Both share one cached backward snapshot per (tenant, seed, depth) — so
`choke` → `leaks` over the same seeds is instant. `--refresh` rebuilds.
Common flags: `--tier-zero/-z` (seed from all Tier Zero), `--domain/-d <domain>`
(scope to one domain's Tier Zero; implies `--tier-zero`), `--max-depth`,
`--fanin`, `--concurrency`.

### `bhe choke [target] --tier-zero [-d domain]`
Ranks the **choke points whose fix cuts the most attack paths** into Tier Zero (or
a given target), and shows the Tier Zero object each one reaches (`reaches_t0`).
```console
$ bhe --mock choke --tier-zero
$ bhe --mock choke --tier-zero -d CORP.LOCAL
```

### `bhe leaks [target] [--tier-zero] [-d domain]`
**Cross-domain / cross-platform leakage** into the crown jewels — boundary
crossings (incl. Azure/Okta/GitHub/Jamf) ranked by volume, flagged when they land
directly on Tier Zero. `bhe --mock leaks --tier-zero`

---

## Search, entities & relationships

### `bhe search <term> [--kind/-k User]`
Search by name or objectid. Substring-friendly: if BHE's index misses a partial,
it falls back to a name `CONTAINS` match, so `spy` finds `SPYS@…`. `--kind` filters
by node kind (e.g. `User`, `Group`, `Computer`).
```console
$ bhe --mock search ali
```

### `bhe entity <name|id> [--kind/-k user]`
A node's **properties**, resolving a name to its objectid and disambiguating
ambiguous names. `--kind` hints/forces the kind. `bhe --mock entity ALICE@CORP.LOCAL`

### `bhe entity <name> --show/-s <relationship>`
Pivot from properties to one of the node's relationships:
`sessions` · `members` · `memberships` · `admin-rights` · `admins` ·
`controllers` · `controllables`.

It lists the related objects; **when the relationship's graph form returns edges,
the permission/right is shown too** as `from | right | to` (e.g. `GenericAll`,
`HasSession`) — otherwise you get the plain object list. See
[Entities & Relationships](Entities-and-Relationships).
```console
$ bhe --mock entity ALICE@CORP.LOCAL --show sessions
$ bhe entity "DOMAIN ADMINS@CORP.LOCAL" --show members
```

---

## Pathfinding (`bhe hunt` + `cypher`)

`hunt` recipes **generate the Cypher for you** and echo it (so it doubles as a way
to learn the query language). `--dry-run` prints the query without running it;
`--all/-a` returns all shortest paths. Sources/targets are resolved by name.

### `bhe hunt path <source> <target>`
Shortest attack path between two principals — shown as the ordered escalation
(`step | from | edge | to`).
```console
$ bhe --mock hunt path ALICE@CORP.LOCAL "DOMAIN ADMINS@CORP.LOCAL"
```

### `bhe hunt tier-zero <source>`
Paths from a principal to ANY Tier Zero target.

### `bhe hunt hybrid <source> [--to/-t azure|okta|github|jamf]`
Paths from an AD principal to ANY cloud/SaaS node — Azure **and** OpenGraph
(Okta/GitHub/Jamf) by default. `--to` scopes to one platform (or a raw label
prefix for a custom OpenGraph source). See [OpenGraph & Hybrid Paths](OpenGraph-Hybrid-Paths).

### `bhe hunt kerberoastable <domain>` / `bhe hunt asrep <domain>`
Enabled users with an SPN (Kerberoastable) / with DontReqPreauth (AS-REP
roastable) in a domain.

### `bhe cypher "<query>"`
Run raw **read-only** Cypher and render the returned nodes. Write clauses are
blocked (see [Read-Only Safety](Read-Only-Safety)).
```console
$ bhe --mock cypher "MATCH (u:User) RETURN u LIMIT 5"
```

---

## Collection & audit

### `bhe clients` / `bhe client <id|fragment|name>`
List collection clients, or one client's detail. `client` accepts a partial id
(e.g. the last segment of the GUID) or the name. `bhe --mock clients`

### `bhe jobs [--current|--finished]` / `bhe job <id>`
Collection jobs, correlated to the collector's name/host. `--current` =
running/queued, `--finished` = completed. `bhe --mock jobs --current`

### `bhe events`
Collection schedules — when each client next collects and what it gathers, in your
local timezone. `bhe --mock events`

### `bhe audit [--logins] [--user/-u <name>] [--last-per-user] [--days N] [--since <iso>] [--action/-a <s>]`
**Platform audit log** — who logged into BHE / took actions, and when (local
time, usernames without domains). `--logins` for auth events; `--last-per-user`
for a "who logged in last" summary. See [Collection Health & Audit](Collection-Health-and-Audit).
```console
$ bhe --mock audit --logins
$ bhe --mock audit --last-per-user
```

---

## Generic & maintenance

### `bhe get <path> [--param/-P key=value ...]`
Read-only GET against **any** API path (always JSON). The escape hatch for
endpoints without a dedicated verb.
```console
$ bhe --mock get /api/v2/available-domains
$ bhe get "/api/v2/domains/<id>/details" -P finding=Kerberoasting -P limit=5
```

### `bhe cache list|clear`
Inspect or clear the backward-scope snapshot cache.

### `bhe keyring set|get|delete [profile]`
Manage stored Token Keys in the OS keyring. See [Configuration](Configuration).

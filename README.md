# bhe

An ergonomic, **read-only** command-line wrapper for the BloodHound Enterprise
(BHE) `/api/v2` REST API — a Python answer to BloodHoundOperator, built for
support engineers and consultants who want to *pull exactly the data they want*
and pipe it into `jq` / `ConvertFrom-Json`.

It pairs **curated verbs** for the common cases with a **generic `bhe get`**
escape hatch so any endpoint is reachable today, before a dedicated verb exists.
Promote the paths you use most into verbs over time.

```console
$ bhe --mock domains
$ bhe --mock jobs --current
$ bhe --mock cypher "MATCH (u:User) RETURN u LIMIT 5"
$ bhe --mock get /api/v2/available-domains -P type=active-directory
$ bhe --profile acme doctor
```

## Safety

Read-only by construction — the same guard as the upstream scout engine:

- the transport allowlist permits only `GET` and the single read-only
  `POST /api/v2/graphs/cypher`; anything else raises before it is signed/sent;
- Cypher is screened for write clauses (`CREATE`/`MERGE`/`SET`/`DELETE`/…) and
  write procedures before sending.

Point it at a customer tenant without fear of mutating state. (Audited
"safe-action" writes may arrive later as an explicit opt-in; v1 is read-only.)

## Commands

| Command | What it does |
|---------|--------------|
| `bhe self` | Authenticated identity + token role |
| `bhe api-version` | Tenant BHE API/server version |
| `bhe profile` | Active connection profile (redacted) |
| `bhe domains [name]` | List domains, or **drill into one** by name/id (findings summary) |
| `bhe posture` | Risk-posture stats |
| `bhe findings <domain>` | A domain's attack-path findings (by **name** or id) |
| `bhe attack-paths` | Attack paths |
| `bhe clients` / `bhe client <id>` | Collection clients |
| `bhe jobs [--current\|--finished]` / `bhe job <id>` | Jobs, **correlated to client name/host** |
| `bhe events` | Scheduled collection events (schedules) |
| `bhe search <term> [--kind User]` | Search by name/objectid |
| `bhe entity <name\|id> [--kind user]` | Entity detail (by **name**, disambiguates) |
| `bhe cypher "<query>"` | Run raw read-only Cypher; show nodes |
| `bhe hunt …` | **Guided attack paths — generates the Cypher for you** (see below) |
| `bhe triage [--by-type]` | **Rank findings across ALL domains — where to start** |
| `bhe choke <target>\|--tier-zero` | **Highest-leverage choke points to remediate** (see below) |
| `bhe leaks <target>\|--tier-zero` | **Cross-domain/platform leakage** into the crown jewels |
| `bhe map <target>\|--tier-zero [-f dot]` | **Condensed, bundled attack graph** (Mermaid/DOT) |
| `bhe get <path> [-P k=v ...]` | **Read any endpoint** (always JSON) |
| `bhe doctor [--strict]` | Tenant health battery (exits non-zero on failure) |
| `bhe bundle [--out file.zip]` | Support bundle (diagnostics + request trace) |
| `bhe info` | Version, active profile, and where bhe stores logs/cache |
| `bhe cache list\|clear` | Inspect / clear the snapshot cache |
| `bhe keyring set\|get\|delete [profile]` | Manage stored Token Keys |

Global flags (before the command): `--profile/-p`, `--mock`, `--profiles-file`,
`--json/-j`, `--version`.

By default results render as Rich tables / key-value views; add `--json` for raw
output. `bhe get` always emits JSON since its shape varies.

## Scoping a large estate — where to start

For big multi-domain, multi-platform tenants, asking the graph for *all* attack
paths times out. `bhe` works the problem the scalable way:

```console
$ bhe triage                       # rank precomputed findings across every domain
$ bhe choke --tier-zero            # the few nodes whose fix cuts the most paths
$ bhe leaks --tier-zero            # which domains/platforms leak into Tier Zero
$ bhe map --tier-zero | pbcopy     # condensed Mermaid graph -> paste into a viewer
```

- **`triage`** uses BHE's precomputed findings (no Cypher) — instant even on a
  million objects. Score = `severity × active_principals × (1 + exposure)`.
- **`choke` / `leaks` / `map`** walk **backward from Tier Zero** one bounded,
  batched, *concurrent* hop at a time — they never issue a global path query.
  High-degree "mass" nodes are detected and truncated without enumeration, and
  choke ranking uses a dominator tree (near-linear). Query count scales with the
  small Tier-Zero-reachable sub-graph, not the size of the tenant.
- The backward snapshot is **cached per tenant** (1h TTL) so `choke` → `leaks` →
  `map` over the same seeds are instant; pass `--refresh` to rebuild.

### Names, not IDs

You never have to paste opaque SIDs/GUIDs. Selectors accept a **name or an id**
everywhere — `bhe findings CORP.LOCAL`, `bhe entity ALICE@CORP.LOCAL`. If a name
is ambiguous, the tool prints the candidates and stops (it never guesses), so
it stays safe to script. `bhe jobs` resolves each job's `client_id` to the
collector's name/hostname for you.

### Guided attack paths (`bhe hunt`)

Don't know Cypher? `bhe hunt` writes it for you and shows the generated query
(so it doubles as a way to learn it):

```console
$ bhe hunt path ALICE@CORP.LOCAL DOMAIN ADMINS@CORP.LOCAL
$ bhe hunt tier-zero ALICE@CORP.LOCAL          # paths to ANY Tier Zero target
$ bhe hunt hybrid ALICE@CORP.LOCAL             # AD -> ANY Azure/Entra node (hybrid)
$ bhe hunt kerberoastable CORP.LOCAL
$ bhe hunt hybrid ALICE@CORP.LOCAL --dry-run   # just print the Cypher, don't run
```

The hybrid recipe matches any `AZ`-labelled (Azure/Entra) target, so it finds a
cross-platform path regardless of which edge crosses the on-prem→cloud boundary.

## Install

No `uv` required — it's a standard package. On **macOS / Linux** with native
Python (≥ 3.10):

```bash
cd bhe
python3 -m venv .venv && source .venv/bin/activate
pip install -e .            # puts `bhe` on your PATH
bhe --mock domains
```

On macOS the Token Key is stored in the **Keychain** via `bhe keyring set`.
(During development you can also use `python -m uv run bhe …` from the repo, but
it's optional.)

Enable tab-completion for your shell (zsh is the macOS default) once:

```bash
bhe --install-completion        # then restart the shell, or: exec zsh
```

`bhe info` shows where everything lives on the current host — on macOS:
`~/Library/Logs/bhe` (request logs) and `~/Library/Caches/bhe/snapshots`
(scope cache). `bhe cache clear` empties the latter.

## Configuration

Connection profiles resolve from environment variables (prefix `BHE_`), an
optional YAML profiles file, and the OS keyring for the Token Key:

```bash
export BHE_TENANT_URL="https://acme.bloodhoundenterprise.io"
export BHE_TOKEN_ID="<token id>"
export BHE_TOKEN_KEY="<token key>"        # or: bhe keyring set acme
bhe self
```

Token Key resolution order: `BHE_TOKEN_KEY` env → OS keyring → `token_key` field
in the YAML profile (discouraged; plaintext).

## Logs & support bundles

Every request is recorded (correlation id, method, endpoint, status, latency,
retry count) and classified into an error taxonomy with remediation hints. For
live profiles the trace is mirrored to a rotating per-tenant log under the user
data dir (`%LOCALAPPDATA%\bhe\logs` on Windows; `$XDG_DATA_HOME/bhe/logs`
otherwise). `bhe bundle` packages a fresh `doctor` run, the request trace, the
redacted profile, and any persisted logs into a single zip for a ticket.

## Development

```bash
pip install -e ".[dev]" && pytest -q       # native Python (macOS/Linux)
# or, with uv from the repo root:
python -m uv run --extra dev pytest -q
```

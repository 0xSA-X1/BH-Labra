# BH-Labra

> The CLI command is **`bhe`** — `BH-Labra` is the project.

An ergonomic, **read-only** command-line wrapper for the BloodHound Enterprise /
Community Edition `/api/v2` API. Built for support engineers, consultants, and
defenders who want to *pull exactly the data they want*, understand a tenant fast,
and pipe results into `jq` / `ConvertFrom-Json` — without ever risking a write.

It pairs **curated verbs** for the common cases with a generic **`bhe get`**
escape hatch, so any endpoint is reachable today even before a dedicated verb
exists. Every selector accepts a **name or an id** (no pasting opaque SIDs), and
ambiguous names print candidates instead of guessing — so it stays safe to script.

```console
$ bhe --mock domains                 # works with bundled fixtures, no tenant needed
$ bhe posture --explain ESSOS.LOCAL  # why is this domain's exposure 42%?
$ bhe choke --tier-zero --domain ESSOS.LOCAL   # the fixes that cut the most paths
$ bhe entity alice@corp.local --show controllers
$ bhe hunt hybrid alice@corp.local   # AD -> Azure / Okta / GitHub / Jamf
```

📖 **[Full docs & use-case walkthroughs are in the Wiki](../../wiki).** This README
is the quick tour; the Wiki has screenshots, end-to-end workflows, and a complete
command reference.

## Why bhe

- **Read-only by construction.** It physically cannot mutate a tenant (see
  [Safety](#safety)) — point it at a customer's production BHE without fear.
- **Built for scale.** Global attack-path queries time out on million-object
  tenants; `bhe` walks *backward from Tier Zero* in bounded, cached, concurrent
  hops, so it answers "where do I start?" in seconds (see [Scoping](#scoping-a-large-estate--where-to-start)).
- **Cross-platform.** Sees Active Directory, Entra/Azure, **and** OpenGraph
  (Okta, GitHub, Jamf) — anything in the graph.
- **Names, not IDs. Transparent. Scriptable.** `--json` on everything; `hunt`
  shows the Cypher it generates so it doubles as a way to learn the query language.

## Safety

Read-only is enforced in the transport, not just by convention:

- the allowlist permits only `GET` and the single read-only
  `POST /api/v2/graphs/cypher`; any other method/path raises **before** the
  request is signed or sent;
- Cypher is screened for write clauses (`CREATE`/`MERGE`/`SET`/`DELETE`/…) and
  write procedures before sending.

A **Read-Only** API role is all you need. The Token Key is used only to *sign*
requests locally — it is never transmitted.

## Commands

Global flags come **before** the command: `--profile/-p`, `--mock`,
`--profiles-file`, `--json/-j`, `--version`. Output is Rich tables / key-value by
default; add `--json` for raw output (`bhe get` is always JSON).

**Identity & host**
| Command | What it does |
|---|---|
| `bhe self` | Authenticated identity + token role |
| `bhe api-version` | Tenant BHE API/server version |
| `bhe profile` | Active connection profile (redacted) |
| `bhe info` | Version, active profile, where logs/cache live |

**Posture, exposure & "where to start"**
| Command | What it does |
|---|---|
| `bhe posture [domain]` | Risk-posture per domain, ranked by **exposure %** |
| `bhe posture <domain> --explain` | **Break down what drives a domain's exposure** |
| `bhe triage [-d domain] [--by-type]` | **Rank attack-path findings across the estate** |
| `bhe tier-zero [domain]` | List the Tier Zero / high-value principals |
| `bhe findings <domain>` | A domain's findings, counted per type |
| `bhe quality [domain]` | **Collection completeness — is the data trustworthy?** |

**Backward attack-path engine**
| Command | What it does |
|---|---|
| `bhe choke --tier-zero [-d domain]` | **Choke points whose fix cuts the most paths** (+ the Tier Zero each reaches) |
| `bhe leaks [--tier-zero] [-d domain]` | **Cross-domain / cross-platform leakage** into the crown jewels |
| `bhe map [--tier-zero] [-d domain] [-f dot]` | **Condensed, bundled attack graph** (Mermaid/DOT) |

**Search, entities & relationships**
| Command | What it does |
|---|---|
| `bhe search <term> [-k User]` | Search by name/objectid (substring-friendly) |
| `bhe entity <name\|id> [-k user]` | Entity properties (by name; disambiguates) |
| `bhe entity <name> --show <rel>` | **A node's relationships**: `sessions`, `members`, `memberships`, `admin-rights`, `admins`, `controllers`, `controllables` |

**Pathfinding (`bhe hunt` — generates the Cypher for you)**
| Command | What it does |
|---|---|
| `bhe hunt path <src> <tgt>` | Shortest attack path between two principals |
| `bhe hunt tier-zero <src>` | Paths from a principal to ANY Tier Zero target |
| `bhe hunt hybrid <src> [--to okta]` | AD → ANY cloud/SaaS node (Azure + Okta/GitHub/Jamf) |
| `bhe hunt kerberoastable <domain>` / `asrep <domain>` | Roastable users in a domain |
| `bhe cypher "<query>"` | Run raw read-only Cypher |

**Collection & audit**
| Command | What it does |
|---|---|
| `bhe clients` / `bhe client <id\|fragment>` | Collection clients (id fragment ok) |
| `bhe jobs [--current\|--finished]` / `bhe job <id>` | Jobs, correlated to client name/host |
| `bhe events` | Collection schedules (local time, readable) |
| `bhe audit [--logins] [--user u] [--last-per-user]` | **Platform audit log** — who logged in / acted, and when |

**Generic & maintenance**
| Command | What it does |
|---|---|
| `bhe get <path> [-P k=v]` | Read any endpoint (always JSON) |
| `bhe doctor [--strict]` | Tenant health battery (non-zero on failure) |
| `bhe bundle [--out f.zip]` | Support bundle (diagnostics + request trace) |
| `bhe cache list\|clear` | Inspect / clear the snapshot cache |
| `bhe keyring set\|get\|delete [profile]` | Manage stored Token Keys |

Add `--help` to any command for its flags.

## Scoping a large estate — where to start

Asking the graph for *all* attack paths times out on a big tenant. `bhe` works it
the scalable way — a typical engagement flow:

```console
$ bhe quality                          # 1. is collection complete enough to trust?
$ bhe posture                          # 2. which domain is most exposed?
$ bhe triage                           # 3. which finding types drive it, estate-wide
$ bhe choke --tier-zero -d CORP.LOCAL  # 4. the highest-leverage fixes for that domain
$ bhe leaks -d CORP.LOCAL              # 5. what leaks in across boundaries
$ bhe map -d CORP.LOCAL | pbcopy       # 6. a picture for the report
```

- **`posture` / `triage` / `quality`** use BHE's precomputed stats (no Cypher) —
  instant even on millions of objects.
- **`choke` / `leaks` / `map`** walk **backward from Tier Zero** one bounded,
  batched, concurrent hop at a time — never a global path query. High-degree
  "mass" nodes are truncated without enumeration; choke ranking uses a dominator
  tree (near-linear). The snapshot is **cached per tenant** (1h TTL), so
  `choke` → `leaks` → `map` over the same seeds are instant (`--refresh` rebuilds).
- `--domain` scopes any of these to one domain — work one domain at a time.

## Install

A standard Python package (≥ 3.10), no `uv` required:

```bash
cd BH-Labra
python3 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip      # editable installs need pip >= 21.3
pip install -e .                          # puts `bhe` on your PATH
bhe --mock domains                        # smoke-test with bundled fixtures
```

(macOS system Python may be 3.9 — if so use Homebrew: `brew install python@3.12`.
`pip install .` works on older pip; you only need `-e` to edit the code.)

Tab-completion (zsh is the macOS default): `bhe --install-completion`, then
restart the shell.

## Configuration (quick version)

A **profile** is one tenant: `base_url` + `token_id` + `token_key`. Get a token
from the BHE UI (Administration → API Tokens); a **Read-Only** role suffices. The
key lives in your OS keyring (macOS Keychain), never in plaintext config.

```bash
export BHE_TENANT_URL="https://acme.bloodhoundenterprise.io"
export BHE_TOKEN_ID="<token id>"
bhe keyring set default        # paste the Token Key (hidden)
bhe doctor                     # verify connectivity + auth
```

For multiple tenants, use a YAML profiles file and `--profile <name>`. Full setup
(env vars, keyring, many-tenant YAML, troubleshooting) is in the
**[Configuration Wiki page](../../wiki/Configuration)**.

## Learning without a tenant

Everything works offline against bundled fixtures with `--mock` — great for trying
commands, demos, and screenshots:

```bash
bhe --mock triage
bhe --mock choke --tier-zero
bhe --mock posture CORP.LOCAL --explain
```

## Development

```bash
pip install -e ".[dev]" && pytest -q       # native Python
# or, from the repo root with uv:
python -m uv run --extra dev pytest -q
```

## License

MIT.

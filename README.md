# BH-Labra

> The CLI command is **`bhe`** (BloodHound Enterprise) — `BH-Labra` is the project.

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
| `bhe domains [name]` | List domains, or **drill into one** by name/id |
| `bhe posture [--history]` | Risk-posture per domain (latest snapshot, ranked) |
| `bhe findings <domain>` | A domain's attack-path findings, per type (by **name** or id) |
| `bhe clients` / `bhe client <id>` | Collection clients |
| `bhe jobs [--current\|--finished]` / `bhe job <id>` | Jobs, **correlated to client name/host** |
| `bhe events` | Scheduled collection events (schedules) |
| `bhe search <term> [--kind User]` | Search by name/objectid |
| `bhe entity <name\|id> [--kind user]` | Entity detail (by **name**, disambiguates) |
| `bhe cypher "<query>"` | Run raw read-only Cypher; show nodes |
| `bhe hunt …` | **Guided attack paths — generates the Cypher for you** (see below) |
| `bhe triage` | **Rank domains by Tier Zero exposure — where to start** |
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
$ bhe triage                       # rank domains by Tier Zero exposure
$ bhe choke --tier-zero            # the few nodes whose fix cuts the most paths
$ bhe leaks --tier-zero            # which domains/platforms leak into Tier Zero
$ bhe map --tier-zero | pbcopy     # condensed Mermaid graph -> paste into a viewer
```

- **`triage`** uses BHE's precomputed posture-stats (no Cypher) — instant even on
  a million objects. Ranks domains by exposure index, then critical-risk count.
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
cd BH-Labra
python3 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip    # editable installs need pip >= 21.3 (PEP 660)
pip install -e .                        # puts `bhe` on your PATH
bhe --mock domains
```

If your `pip` is too old to upgrade, `pip install .` (non-editable) works on
older pip and still installs the `bhe` command — you only need `-e` for editing
the code. (macOS system Python may be 3.9; if so, use Homebrew Python:
`brew install python@3.12 && python3.12 -m venv .venv`.)

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

A **profile** is one tenant: `base_url` + `token_id` + `token_key`. The token key
is never kept in plaintext config — it lives in your OS keyring (macOS Keychain)
and is only ever used to *sign* requests locally (it is never transmitted).
Resolution order for the key: `BHE_TOKEN_KEY` env → OS keyring (by profile name)
→ `token_key:` in the YAML (discouraged).

Get an API token from the BHE UI (Administration → API Tokens); it shows a
**Token ID** and a **Token Key** (the key is shown once). A **Read-Only** role is
all you need.

### One tenant (env + keyring)

```bash
# add to ~/.zshrc to persist across shells
export BHE_TENANT_URL="https://acme.bloodhoundenterprise.io"
export BHE_TOKEN_ID="<token id>"

bhe keyring set default        # paste the Token Key (stored in the Keychain)
bhe doctor                     # verify connectivity + auth
```

### Many tenants (YAML profiles)

Create `~/.config/bhe/profiles.yaml`:

```yaml
profiles:
  acme:
    base_url: https://acme.bloodhoundenterprise.io
    token_id: <acme token id>
  contoso:
    base_url: https://contoso.bloodhoundenterprise.io
    token_id: <contoso token id>
```

Point `bhe` at it (persist in `~/.zshrc`), then store each tenant's key in the
Keychain under the **same name** as its YAML entry:

```bash
echo 'export BHE_PROFILES_FILE="$HOME/.config/bhe/profiles.yaml"' >> ~/.zshrc
export BHE_PROFILES_FILE="$HOME/.config/bhe/profiles.yaml"

bhe keyring set acme           # paste each tenant's Token Key (input is hidden)
bhe keyring set contoso

bhe --profile acme doctor
bhe --profile contoso triage
```

The profile **name is the join key** — it must match in all three places:
`acme:` in the YAML, `bhe keyring set acme`, and `--profile acme`.

> **Notes**
> - `base_url` is the bare origin — no trailing slash, no `/api/...`.
> - Don't set `BHE_TOKEN_KEY` while using multiple YAML profiles; it would
>   override *every* profile's key.
> - Verify any profile with `bhe --profile acme info` (shows `token key: set`)
>   and `bhe --profile acme self` (signs a live request → your identity/role).
> - The keyring prompt hides input; paste the key and press Enter.

## Development

```bash
pip install -e ".[dev]" && pytest -q       # native Python (macOS/Linux)
# or, with uv from the repo root:
python -m uv run --extra dev pytest -q
```

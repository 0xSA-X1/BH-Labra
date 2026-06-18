# Use Case: Entities & Relationships

**Scenario:** you've found an interesting principal (a choke point, a flagged
finding, a suspicious account) and want to drill into it — its properties *and* its
relationships (sessions, group membership, admin rights, and the ACL attack
surface).

## Find it — `search`

```console
$ bhe search alice                 # name or objectid; substring-friendly
$ bhe search alice --kind User     # filter by kind
```

Search is forgiving: if BHE's index misses a partial, it falls back to a name
`CONTAINS` match, so `spy` finds `SPYS@ESSOS.LOCAL`. (`SPYS` is a *Group*, which is
why `spy --kind User` correctly returns nothing.)

> 📸 **Screenshot:** `bhe search alice`

## Inspect it — `entity`

```console
$ bhe entity ALICE@CORP.LOCAL          # properties (by name; disambiguates)
$ bhe entity alice                     # partial names resolve too
```

Names resolve to objectids automatically; an ambiguous name prints candidates and
stops rather than guessing.

## Pivot to relationships — `entity --show <aspect>`

The default view is properties. `--show` pivots to a relationship:

| `--show` | Shows |
|---|---|
| `sessions` | Where the principal has sessions / who logged into a host |
| `members` | A group's members |
| `memberships` | Groups the principal belongs to |
| `admin-rights` | Where the principal is a local admin |
| `admins` | A computer's local admins |
| `controllers` | **Who can control this object** (inbound ACLs) |
| `controllables` | **What this object can control** (outbound ACLs) |

```console
$ bhe entity ALICE@CORP.LOCAL --show sessions
$ bhe entity "DOMAIN ADMINS@CORP.LOCAL" --show members
$ bhe entity DC01.CORP.LOCAL --show admins
$ bhe entity SPYS@ESSOS.LOCAL --show controllers
$ bhe entity SPYS@ESSOS.LOCAL --show controllables
```

Where the endpoint returns edges, the **permission/right** is shown
(`from | right | to`), e.g. `DOMAIN ADMINS -[GenericAll]-> SPYS`. Otherwise you get
the related objects.

> 📸 **Screenshot:** `bhe entity SPYS@ESSOS.LOCAL --show controllers`

## Why this matters for remediation

- `controllers` / `controllables` are the **ACL attack surface** — exactly what an
  attacker abuses and what you remediate.
- `sessions` is AD **logon activity** (where credentials have been seen) — useful
  for blast-radius and for the "when did X log in" question (distinct from the BHE
  *platform* logins in [`audit`](Use-Case-Collection-Health-and-Audit)).
- Pair with `bhe choke` (find the high-leverage object) → `entity --show
  controllers` (see who can abuse it) → `bhe hunt tier-zero` (the full path).

## Tip: JSON for tooling

```console
$ bhe --json entity ALICE@CORP.LOCAL --show controllers | jq '.'
```

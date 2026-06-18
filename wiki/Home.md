# BH-Labra (`bhe`) Wiki

**`bhe`** is an ergonomic, **read-only** CLI for the BloodHound Enterprise /
Community Edition API. It turns a tenant's attack-path data into fast, scriptable
answers — *where do I start, what's exposed, who can reach Tier Zero, is the data
even trustworthy* — without any risk of changing the tenant.

This Wiki shows what the tool can do, with copy-paste examples you can run (and
screenshot) yourself.

> 💡 Every example here also works **offline** with `--mock` (bundled demo data,
> no tenant or credentials needed). Swap `--mock` for `--profile <tenant>` to run
> it for real. See [Mock Mode](Mock-Mode).

---

## Start here

| If you want to… | Read |
|---|---|
| Install it | [Installation](Installation) |
| Connect it to a tenant | [Configuration](Configuration) |
| See every command | [Command Reference](Command-Reference) |
| Understand the read-only guarantee | [Read-Only Safety](Read-Only-Safety) |
| Try it with no tenant | [Mock Mode](Mock-Mode) |

## Use-case walkthroughs

End-to-end, screenshot-friendly workflows:

1. **[Triaging a New Customer](Use-Case-New-Customer-Triage)** — from "I just got
   access" to a prioritized remediation list.
2. **[Exposure Deep-Dive](Use-Case-Exposure-Deep-Dive)** — what a domain's
   exposure % actually means and what drives it.
3. **[Attack-Path Hunting](Use-Case-Attack-Path-Hunting)** — choke points,
   leakage, maps, and guided Cypher.
4. **[OpenGraph & Hybrid Paths](Use-Case-OpenGraph-Hybrid-Paths)** — Okta, GitHub,
   Jamf, and Azure crossings.
5. **[Collection Health & Audit](Use-Case-Collection-Health-and-Audit)** — is the
   data complete, and who's been in the platform.
6. **[Entities & Relationships](Use-Case-Entities-and-Relationships)** — drill
   into a principal: sessions, members, admin rights, ACLs.

## The 60-second tour

```console
$ bhe quality                          # is collection complete enough to trust?
$ bhe posture                          # which domains are most exposed (as %)
$ bhe triage                           # which finding types drive it, estate-wide
$ bhe choke --tier-zero -d CORP.LOCAL  # the highest-leverage fixes for one domain
$ bhe leaks -d CORP.LOCAL              # what leaks in across boundaries
$ bhe map -d CORP.LOCAL | pbcopy       # a graph for the report
```

## What makes it different

- **Read-only by construction** — it physically cannot write to a tenant.
- **Scales** — walks *backward from Tier Zero* in bounded, cached, concurrent hops
  instead of issuing global path queries that time out.
- **Cross-platform** — AD, Entra/Azure, and OpenGraph (Okta/GitHub/Jamf).
- **Names not IDs**, ambiguity surfaced not guessed, `--json` everywhere, and
  `hunt` shows the Cypher it writes so you learn the query language as you go.

## Command map

- **Posture / start-here:** `posture`, `posture --explain`, `triage`, `tier-zero`,
  `findings`, `quality`
- **Engine:** `choke`, `leaks`, `map`
- **Entities:** `search`, `entity`, `entity --show <rel>`
- **Pathfinding:** `hunt path|tier-zero|hybrid|kerberoastable|asrep`, `cypher`
- **Collection:** `clients`, `client`, `jobs`, `job`, `events`, `audit`
- **Generic / host:** `get`, `self`, `api-version`, `profile`, `info`, `doctor`,
  `bundle`, `cache`, `keyring`

Full details in the [Command Reference](Command-Reference).

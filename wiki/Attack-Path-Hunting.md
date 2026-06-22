**Scenario:** you want to understand and remediate the attack paths into Tier Zero
— at scale, without global path queries that time out. (You ran `choke` and `leaks`
in the [triage workflow](New-Customer-Triage); this is the deep dive —
how the engine works, plus pulling specific paths with `hunt`.)

`bhe` gives you two complementary tools:

- the **backward engine** (`choke` / `leaks`) — the big-picture, scalable view of
  everything funneling into the crown jewels;
- **`hunt`** + **`cypher`** — targeted, specific paths between objects.

## The backward engine — `choke`, `leaks`

These seed at Tier Zero and walk *backward* one bounded, batched, concurrent hop
at a time, so they scale to million-object tenants. They share one cached snapshot
per (tenant, seed, depth), so running both over the same seeds is instant.

### `choke` — the highest-leverage fixes

```console
$ bhe choke --tier-zero
$ bhe choke --tier-zero --domain CORP.LOCAL    # one domain at a time
```

Ranks the objects whose removal **disconnects the most principals** from Tier
Zero, and shows the Tier Zero asset each one `reaches_t0`. This is your prioritized
remediation list: "fix this object → cut N% of paths."

> 📸 **Screenshot:** 
<img width="1036" height="249" alt="bhe choke essos" src="https://github.com/user-attachments/assets/63b5fb64-c08f-4a5a-97ea-f8489ea6c028" />

### `leaks` — boundary crossings

```console
$ bhe leaks --tier-zero
$ bhe leaks --domain CORP.LOCAL
```

Aggregates edges whose endpoints are in **different domains or platforms** —
where one domain (or Azure/Okta/GitHub/Jamf) reaches into another's crown jewels.
Flags crossings that land directly on Tier Zero.

**Tuning:** `--max-depth` (hops back), `--fanin` (mass-node threshold),
`--concurrency`, `--refresh` (rebuild the cache).

## Targeted paths — `hunt`

`hunt` writes the Cypher for you and echoes it (so it teaches you the query
language). Sources/targets are resolved by name; `--dry-run` prints the query
without running it; `--all` returns all shortest paths.

```console
$ bhe hunt path ALICE@CORP.LOCAL "DOMAIN ADMINS@CORP.LOCAL"
$ bhe hunt tier-zero ALICE@CORP.LOCAL      # to ANY Tier Zero target
$ bhe hunt kerberoastable CORP.LOCAL
$ bhe hunt path ALICE@CORP.LOCAL "DOMAIN ADMINS@CORP.LOCAL" --dry-run
```

Path results render as the **ordered escalation** — read top to bottom:

```
step | from                | edge       | to
  1  | ALICE@CORP.LOCAL    | MemberOf   | HELPDESK@CORP.LOCAL
  2  | HELPDESK@CORP.LOCAL | GenericAll | DOMAIN ADMINS@CORP.LOCAL
```

For cross-platform / hybrid paths (Azure, Okta, GitHub, Jamf) see
[OpenGraph & Hybrid Paths](OpenGraph-Hybrid-Paths).

## Raw Cypher

When a recipe doesn't fit, write your own read-only Cypher:

```console
$ bhe cypher "MATCH (u:User) WHERE u.hasspn = true RETURN u LIMIT 25"
```

Write clauses are blocked — see [Read-Only Safety](Read-Only-Safety).

## Tips

- Start broad (`choke --tier-zero`), then scope to a domain (`-d`) to keep the
  graph small and the output focused.
- Use `bhe entity <choke-point> --show controllers` to see *who/what* can abuse a
  choke point ([Entities & Relationships](Entities-and-Relationships)).
- A choke point that `reaches_t0` for several crown jewels is often the most urgent
  fix.

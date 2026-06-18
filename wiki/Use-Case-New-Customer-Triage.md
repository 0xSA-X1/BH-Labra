# Use Case: Triaging a New Customer

**Scenario:** you've just been given read-only API access to a customer's
BloodHound Enterprise tenant. You have a 10+ domain, multi-platform estate and a
limited window. Where do you start?

This is the flagship `bhe` workflow: **trust the data → triage the estate → go
deep on the worst domain → hand off a prioritized fix list.** All read-only.

> Every step below works with `--mock` for practice; against a tenant, drop
> `--mock` and add `--profile <name>` if you use multiple profiles.

---

## 0. Connect and sanity-check

```console
$ bhe self        # confirms auth + your token's role
$ bhe doctor      # read-only health battery
```
> 📸 **Screenshot:** `bhe doctor`

## 1. Trust the data *before* you trust the findings

BHE's analysis is only as good as collection. If sessions or local-group data
wasn't collected, whole classes of edges are invisible — and your "findings" are
blind to them.

```console
$ bhe quality                 # % of sessions & local-admins collected, estate-wide
$ bhe clients                 # are the collectors checking in?
$ bhe jobs --finished         # did the last runs succeed?
$ bhe events                  # what each collector gathers, and when next
```

**Look for:** low session/local-group completeness, stale collectors, failed jobs.
Fix collection gaps first, or caveat your findings.

> 📸 **Screenshot:** `bhe quality`

## 2. Triage the estate — which domain, what kind of problem

```console
$ bhe posture            # domains ranked by exposure %
$ bhe triage --by-type   # finding types ranked across ALL domains
$ bhe triage             # every (finding, domain), worst first
```

**`posture`** tells you *which domain* is worst (highest exposure %).
**`triage`** tells you *what's driving it* — the cross-domain "fix these first"
list the per-domain UI can't produce.

> 📸 **Screenshot:** `bhe posture` and `bhe triage`

## 3. Pick the top domain and go deep

Work one domain at a time (`-d` / `--domain` scopes most commands):

```console
$ bhe posture CORP.LOCAL --explain    # WHY is this domain's exposure what it is?
$ bhe triage -d CORP.LOCAL            # its findings, ranked
$ bhe choke --domain CORP.LOCAL       # <- the remediation gold
$ bhe leaks --domain CORP.LOCAL       # what leaks in across boundaries
$ bhe map --domain CORP.LOCAL         # the funnel as a diagram for the report
```

**`bhe choke`** is the one to lead remediation with: it ranks the objects where
*one fix cuts the most attack paths into Tier Zero*, and shows the Tier Zero
asset each reaches. That's an effort-aware, highest-leverage fix list.

> 📸 **Screenshot:** `bhe choke --domain CORP.LOCAL`

## 4. Hand off the remediation

Work down the `choke` list — each row is "fix this object → cut N% of paths to a
crown jewel." Use `bhe entity <name> --show controllers` and
`bhe hunt tier-zero <name>` (see [Attack-Path Hunting](Use-Case-Attack-Path-Hunting))
to capture the exact paths for the report, and `bhe map` for the picture.

---

## Cheat sheet

```console
bhe quality                          # 1. is the data good?
bhe posture ; bhe triage             # 2. worst domain + what drives it
bhe choke --domain <D>               # 3. the prioritized fix list
bhe leaks --domain <D>               # 4. boundary problems
bhe map --domain <D> | pbcopy        # 5. the diagram
```

**One caveat:** `triage`/`findings`/`posture --explain` rely on findings data,
which exists for collected **AD** domains — Entra/Azure tenants show up as
"skipped." The `choke`/`leaks`/`map` engine works on every platform.

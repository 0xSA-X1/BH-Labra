# Use Case: Triaging a New Customer

**Scenario:** you've just been given read-only API access to a customer's
BloodHound Enterprise tenant — a 10+ domain, multi-platform estate and a limited
window. Where do you start?

This is the flagship `bhe` workflow and the **spine of this Wiki**: trust the data
→ triage the estate → go deep on the worst domain → hand off a prioritized fix
list. Each step links to the page that teaches that command in full.

> Works with `--mock` for practice; against a tenant, drop `--mock` (and add
> `--profile <name>` for multiple profiles).

---

## 0. Connect

```console
$ bhe self        # confirms auth + your token's role
$ bhe doctor      # read-only health battery
```
> 📸 **Screenshot:** `bhe doctor`

## 1. Trust the data first

BHE's analysis is only as good as collection — if sessions or local-groups weren't
collected, whole classes of edges are invisible.

```console
$ bhe quality     # % of sessions & local-admins collected
```

If completeness is low, fix collection (or caveat your findings) before going
further. → Full collector / job / schedule checks:
**[Collection Health & Audit](Use-Case-Collection-Health-and-Audit)**.

## 2. Triage the estate

```console
$ bhe posture            # domains ranked by exposure %
$ bhe triage --by-type   # finding types ranked across ALL domains
$ bhe triage             # every (finding, domain), worst first
```

`posture` tells you *which domain* is worst; `triage` tells you *what's driving
it* — the cross-domain "fix these first" list the per-domain UI can't produce.
→ What the exposure % actually means: **[Exposure Deep-Dive](Use-Case-Exposure-Deep-Dive)**.

> 📸 **Screenshot:** `bhe triage`

## 3. Go deep on the worst domain

Work one domain at a time (`-d` / `--domain` scopes most commands):

```console
$ bhe posture CORP.LOCAL --explain    # why is its exposure what it is?
$ bhe triage -d CORP.LOCAL            # its findings, ranked
$ bhe choke --domain CORP.LOCAL       # the highest-leverage fixes
$ bhe leaks --domain CORP.LOCAL       # what leaks in across boundaries
```

**`bhe choke` is the one to lead remediation with** — it ranks the objects whose
fix cuts the most paths into Tier Zero. → The exposure breakdown:
**[Exposure Deep-Dive](Use-Case-Exposure-Deep-Dive)**; how the choke/leaks engine
works and how to pull specific paths: **[Attack-Path Hunting](Use-Case-Attack-Path-Hunting)**.

## 4. Hand off the remediation

Work down the `choke` list — each row is "fix this object → cut N% of paths to a
crown jewel." For each, capture *who can abuse it* with
**[`entity --show controllers`](Use-Case-Entities-and-Relationships)** and the exact
path with **[`hunt tier-zero`](Use-Case-Attack-Path-Hunting)**.

---

## Cheat sheet

```console
bhe quality                          # 1. is the data good?
bhe posture ; bhe triage             # 2. worst domain + what drives it
bhe choke --domain <D>               # 3. the prioritized fix list
bhe leaks --domain <D>               # 4. boundary problems
```

**Caveat:** `triage` / `findings` / `posture --explain` rely on findings data,
which exists for collected **AD** domains — Entra/Azure tenants show up as
"skipped." The `choke` / `leaks` engine works on every platform.

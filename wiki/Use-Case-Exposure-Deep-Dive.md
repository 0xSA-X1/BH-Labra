# Use Case: Exposure Deep-Dive

**Scenario:** the [triage workflow](Use-Case-New-Customer-Triage) ranked
`ESSOS.LOCAL` at **86%** exposure (from `bhe posture`). What does that number mean,
and what's driving it?

## What "exposure" means

A domain's **exposure** is the share of its principals (users, computers, groups)
that can reach a **Tier Zero** (most-privileged) asset through *at least one*
attack path. BHE computes it server-side; `bhe` shows it as a percentage, the same
value as the web GUI — it's the column `bhe posture` ranks on.

## Breaking it down: `--explain`

```console
$ bhe posture ESSOS.LOCAL --explain
```

You get the headline (exposure %, Tier Zero count, critical findings) plus the
**findings that create that exposure**, each with:

- **severity** — the finding type's inherent danger (critical/high/moderate/low).
- **principals** — how many objects are affected.
- **exposure** — the % of principals this finding exposes to Tier Zero (BHE's
  `ExposurePercentage`).
- **impact** — how much of the attack-path surface it accounts for
  (`ImpactPercentage`).

Fix high-severity, high-exposure rows first — they cut the most paths.

> 📸 **Screenshot:** `bhe posture ESSOS.LOCAL --explain`

## Why the per-finding numbers don't add up to the total

This trips everyone up: if the domain is 86%, why do individual findings show
77%, 73%, etc., and why don't they sum to 86%?

**Because they're overlapping populations, not slices of a pie.** The *same*
principal is usually exposed by several findings at once (they can reach Tier Zero
via ESC1 *and* GenericAll *and* a golden cert…). So:

- the domain's 86% is the **union** — principals who can reach Tier Zero via *any*
  path;
- it's **larger** than the biggest single finding (a finding can't expose more
  than the union);
- and **far smaller** than the sum (which double-counts the overlap).

Think of doors into a vault: 86% of people can get in through *some* door; door A
works for 77% of them — but most people can use several doors, so you don't add
the doors up.

## Severity vs exposure are different axes

A finding can be **critical severity but low exposure** (dangerous if abused, but
few principals can), or **moderate severity but high exposure** (lots of
principals, lower individual blast radius). Read both columns together.

## Notes

- `exposure`/`impact` are read from a representative affected principal per
  finding; for findings with many principals, treat them as indicative.
- `ExposurePercentage` is populated for relationship-type findings when butterfly
  analysis is enabled; list-type findings (e.g. Kerberoasting) may show `0%`
  exposure while still carrying a meaningful `impact`.

## See also

- [Attack-Path Hunting](Use-Case-Attack-Path-Hunting) — turn a finding into the
  actual paths and choke points.

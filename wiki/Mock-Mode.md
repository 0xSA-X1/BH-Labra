# Mock Mode (`--mock`)

`--mock` serves bundled JSON fixtures instead of calling a tenant — **no
credentials, no network**. It's the fastest way to learn the tool, demo it, or
capture clean screenshots that contain no customer data.

```console
$ bhe --mock domains
$ bhe --mock triage
$ bhe --mock choke --tier-zero
$ bhe --mock posture CORP.LOCAL --explain
$ bhe --mock hunt hybrid alice@corp.local --dry-run
```

Put `--mock` **before** the command, like any global flag.

## The demo estate

Mock mode models a small, realistic estate so the full engine runs end to end:

- **Domains:** `CORP.LOCAL`, `DEV.CORP.LOCAL`, `contoso.onmicrosoft.com` (Azure).
- **A Tier-Zero funnel:** `AUTHENTICATED USERS` / `ALICE` / `BOB` → `HELPDESK`
  → `DOMAIN ADMINS@CORP.LOCAL`, plus `BACKUP-SVC` (DCSync) and a cross-domain
  `DEV-ADMIN@DEV` → `DOMAIN ADMINS@CORP.LOCAL` leak. So `choke` correctly flags
  HELPDESK as the dominant choke point, and `leaks` shows the DEV→CORP crossing.
- **Findings** per domain (DCSync / Kerberoasting / ASREPRoasting) so `triage`,
  `findings`, and `posture --explain` produce real rankings.
- **Clients, jobs, events, audit, and data-quality** fixtures so the collection
  and audit commands render.

## What mock mode is good for

- **Learning** — run any command and see its shape before touching a tenant.
- **Demos** — show the tool end to end with zero setup.
- **Docs/screenshots** — reproducible, sanitized output.
- **Development** — the test suite runs entirely on these fixtures (no network).

## What it is *not*

Mock data is a tiny synthetic estate, not your tenant. Numbers and names are
illustrative. For real analysis, configure a profile ([Configuration](Configuration))
and drop the `--mock` flag.

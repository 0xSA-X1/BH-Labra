**Scenario:** the "trust the data" step of the
[triage workflow](New-Customer-Triage) opens with `bhe quality`; this page
is the full version — confirm collection is complete and current, and see who's
been using the BHE platform.

## Is the data complete? — `quality`

```console
$ bhe quality                 # estate completeness: % of sessions & local-admins collected
$ bhe quality CORP.LOCAL      # one domain's collection counts + completeness %
```

Low session or local-group completeness means the attack-path analysis is blind to
those edges — fix collection before trusting findings.

> 📸 **Screenshot:** 
<img width="494" height="102" alt="bhe mock quality" src="https://github.com/user-attachments/assets/5cf003ae-5084-437a-add8-ceca216b4931" />

## Are the collectors healthy? — `clients`, `jobs`, `events`

```console
$ bhe clients                 # registered collectors + last check-in
$ bhe jobs                    # recent collection runs, correlated to client name/host
$ bhe jobs --current          # running/queued
$ bhe jobs --finished         # completed
$ bhe job <id>                # one run's detail
$ bhe events                  # schedules: when each client next collects, and what
```

**Look for:** collectors that haven't checked in, failed jobs, schedules that
don't collect sessions/local-groups (which would explain low `quality`). `events`
shows times in your local timezone and what each run gathers.

`bhe client` accepts a partial id (e.g. the last segment of the GUID) or the name,
so you don't have to copy the whole client id out of the terminal:

```console
$ bhe client 41bf0d42c999     # an id fragment resolves to the full client
```

> 📸 **Screenshot:** 
<img width="988" height="132" alt="bhe mock jobs" src="https://github.com/user-attachments/assets/8a50c031-7624-4a49-95f4-70db6d7851d8" />

## Who's been in the platform? — `audit`

The BHE **platform audit log** — logins and actions taken in the BHE app itself
(distinct from AD logon activity, which is `entity --show sessions`).

```console
$ bhe audit                       # recent platform activity, newest first (local time)
$ bhe audit --logins              # authentication events only
$ bhe audit --last-per-user       # most recent event per actor (who logged in last)
$ bhe audit --user grayson        # scope to one actor (name/email substring)
$ bhe audit --days 30             # widen the window (default 7)
```

Usernames are shown without their domain; times are in your local timezone.

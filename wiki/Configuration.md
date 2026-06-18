# Configuration

A **profile** is one tenant: `base_url` + `token_id` + `token_key`. The Token Key
is never kept in plaintext config — it lives in your OS keyring (macOS Keychain /
Windows Credential Manager) and is only ever used to **sign** requests locally
(it is never transmitted).

## Get an API token

In the BHE UI: **Administration → API Tokens → Create Token**. It shows a
**Token ID** and a **Token Key** (the key is displayed once — copy it). A
**Read-Only** role is all `bhe` needs.

## One tenant (env + keyring)

```bash
# add to ~/.zshrc (or your shell profile) to persist
export BHE_TENANT_URL="https://acme.bloodhoundenterprise.io"
export BHE_TOKEN_ID="<token id>"

bhe keyring set default        # paste the Token Key (input is hidden)
bhe doctor                     # verify connectivity + auth
```

## Many tenants (YAML profiles)

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

Point `bhe` at it and store each tenant's key under the **same name** as its YAML
entry:

```bash
echo 'export BHE_PROFILES_FILE="$HOME/.config/bhe/profiles.yaml"' >> ~/.zshrc
export BHE_PROFILES_FILE="$HOME/.config/bhe/profiles.yaml"

bhe keyring set acme           # paste each tenant's Token Key
bhe keyring set contoso

bhe --profile acme doctor
bhe --profile contoso triage
```

The profile **name is the join key** — it must match in all three places:
`acme:` in the YAML, `bhe keyring set acme`, and `--profile acme`.

## Key resolution order

For a given profile, the Token Key is found in this order:

1. `BHE_TOKEN_KEY` environment variable
2. OS keyring, by profile name (recommended)
3. `token_key:` in the YAML (discouraged — plaintext)

## Verify a profile

```console
$ bhe --profile acme info       # shows "token key: set" + base_url
$ bhe --profile acme self       # signs a live request -> your identity & role
```

## Notes & gotchas

- `base_url` is the bare origin — **no** trailing slash, **no** `/api/...`.
- Don't set `BHE_TOKEN_KEY` while using multiple YAML profiles — it would override
  *every* profile's key.
- The keyring prompt hides input; paste the key and press Enter.
- No keyring backend on the host? Use `BHE_TOKEN_KEY` for that session, or (least
  preferred) a `token_key:` in the YAML.

## See also

- [Read-Only Safety](Read-Only-Safety) — why a Read-Only token is enough.
- [Mock Mode](Mock-Mode) — no token needed at all.

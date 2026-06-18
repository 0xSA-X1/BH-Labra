# Installation

`bhe` is a standard Python package (Python **≥ 3.10**). No `uv` required.

## macOS / Linux

```bash
cd BH-Labra
python3 -m venv .venv && source .venv/bin/activate
python -m pip install --upgrade pip      # editable installs need pip >= 21.3 (PEP 660)
pip install -e .                          # puts `bhe` on your PATH
bhe --mock domains                        # smoke-test with bundled fixtures
```

- **macOS system Python is often 3.9** — too old. Install a newer one:
  ```bash
  brew install python@3.12
  python3.12 -m venv .venv && source .venv/bin/activate
  ```
- If your `pip` is too old to upgrade, `pip install .` (non-editable) still
  installs the `bhe` command — you only need `-e` to edit the source.

## Windows (PowerShell)

```powershell
cd BH-Labra
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .
bhe --mock domains
```

## Verify

```console
$ bhe --version
$ bhe --mock doctor          # runs the health battery against demo fixtures
```

> 📸 **Screenshot:** `bhe --mock domains`

## Tab completion

```bash
bhe --install-completion     # then restart the shell (zsh is the macOS default)
```

## Where it stores things

`bhe info` prints the active profile and on-disk locations for the current host:

- **Request logs** — macOS `~/Library/Logs/bhe`, Windows `%LOCALAPPDATA%\bhe\logs`,
  Linux `$XDG_*`/`~/.local`.
- **Snapshot cache** — macOS `~/Library/Caches/bhe/snapshots`, Windows
  `%LOCALAPPDATA%\bhe\snapshots`. Clear it with `bhe cache clear`.

## Next

- [Configuration](Configuration) — connect to a tenant.
- [Mock Mode](Mock-Mode) — try everything with no tenant.

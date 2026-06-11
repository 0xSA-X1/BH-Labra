"""Configuration: multi-tenant profiles with OS-keyring-backed secrets.

A *profile* is one BHE tenant.  Non-secret fields (tenant URL, Token ID) come
from environment/`.env` or a YAML profiles file; the secret Token Key is read
from the OS keyring by default, with an env/YAML fallback.  The Token Key is
never written to logs.

The keyring backend is the OS-native one on every platform: macOS Keychain,
Windows Credential Manager, and the Secret Service (GNOME Keyring / KWallet) on
Linux.  On a **headless Linux** box there may be no Secret Service running - in
that case keyring reads degrade to ``None`` and you simply supply the key via
``BHE_TOKEN_KEY`` (the resolution order below makes this seamless for
testing while keyring remains the production end goal).

Resolution order for the Token Key (first hit wins):
    1. ``BHE_TOKEN_KEY`` environment variable (CI / headless Linux testing)
    2. OS keyring entry for the profile name (recommended for daily use)
    3. ``token_key`` field in the YAML profile (discouraged; plaintext)
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("bhe")

KEYRING_SERVICE = "bhe"
DEFAULT_PROFILE = "default"


class TenantProfile(BaseModel):
    """A single BHE tenant connection profile."""

    name: str = DEFAULT_PROFILE
    base_url: str = ""
    token_id: str = ""
    token_key: str | None = Field(default=None, repr=False)  # never echo the secret
    mock: bool = False

    def is_complete(self) -> bool:
        """True when enough is set to connect (or running in mock mode)."""
        if self.mock:
            return True
        return bool(self.base_url and self.token_id and self.token_key)

    def redacted(self) -> dict[str, str | bool]:
        """A log-safe view of the profile (token key masked)."""
        return {
            "name": self.name,
            "base_url": self.base_url,
            "token_id": self.token_id,
            "token_key": "***" if self.token_key else "(unset)",
            "mock": self.mock,
        }


class _EnvSettings(BaseSettings):
    """Default-profile fields sourced from environment / ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="BHE_", env_file=".env", extra="ignore"
    )

    tenant_url: str = ""
    token_id: str = ""
    token_key: str | None = None
    mock: bool = False
    profiles_file: str | None = None


# ---------------------------------------------------------------------------
# Keyring helpers
# ---------------------------------------------------------------------------

class KeyringUnavailable(RuntimeError):
    """Raised when no usable OS keyring backend is present (e.g. headless Linux)."""


def keyring_available() -> bool:
    """Return True if a real (non-fail) keyring backend is active.

    On headless Linux with no Secret Service, ``keyring`` selects a "fail"
    backend; we treat that as unavailable so callers can fall back to env vars.
    """
    try:
        import keyring
        from keyring.backends import fail

        backend = keyring.get_keyring()
        return not isinstance(backend, fail.Keyring)
    except Exception:  # noqa: BLE001 - any import/backend error means unavailable
        return False


def set_keyring_token(profile_name: str, token_key: str) -> None:
    """Store a profile's Token Key in the OS keyring.

    Raises:
        KeyringUnavailable: If no usable keyring backend exists. Callers should
            tell the user to use ``BHE_TOKEN_KEY`` / a YAML profile instead.
    """
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, profile_name, token_key)
    except Exception as exc:  # noqa: BLE001
        raise KeyringUnavailable(str(exc)) from exc


def get_keyring_token(profile_name: str) -> str | None:
    """Read a profile's Token Key from the OS keyring (None if unset)."""
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, profile_name)
    except Exception as exc:  # keyring backend unavailable, etc.
        logger.debug("keyring lookup failed for %s: %s", profile_name, exc)
        return None


def delete_keyring_token(profile_name: str) -> None:
    """Remove a profile's Token Key from the OS keyring (best effort)."""
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, profile_name)
    except Exception as exc:
        logger.debug("keyring delete failed for %s: %s", profile_name, exc)


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------

def _load_yaml_profiles(path: Path) -> dict[str, TenantProfile]:
    """Parse a YAML profiles file into name -> TenantProfile."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    profiles: dict[str, TenantProfile] = {}
    for name, fields in (raw.get("profiles") or {}).items():
        fields = dict(fields or {})
        fields["name"] = name
        profiles[name] = TenantProfile(**fields)
    return profiles


def load_profile(
    name: str | None = None,
    *,
    mock: bool | None = None,
    profiles_file: str | Path | None = None,
) -> TenantProfile:
    """Resolve a single connection profile with its Token Key filled in.

    Args:
        name: Profile name; defaults to ``BHE_*`` env (the ``default`` profile)
            unless a YAML profiles file is provided and names a different one.
        mock: Force mock mode on/off; ``None`` means honour the profile/env value.
        profiles_file: Optional path to a YAML profiles file (overrides
            ``BHE_PROFILES_FILE``).

    Returns:
        A :class:`TenantProfile` ready to hand to ``BHEClient.connect``.
    """
    env = _EnvSettings()
    yaml_path = (
        Path(profiles_file)
        if profiles_file
        else (Path(env.profiles_file) if env.profiles_file else None)
    )

    profile: TenantProfile
    if yaml_path and yaml_path.is_file():
        profiles = _load_yaml_profiles(yaml_path)
        chosen = name or DEFAULT_PROFILE
        if chosen not in profiles:
            raise KeyError(
                f"Profile '{chosen}' not found in {yaml_path}. "
                f"Available: {', '.join(profiles) or '(none)'}"
            )
        profile = profiles[chosen]
    else:
        profile = TenantProfile(
            name=name or DEFAULT_PROFILE,
            base_url=env.tenant_url,
            token_id=env.token_id,
            token_key=env.token_key,
            mock=env.mock,
        )

    # Token Key resolution: env > keyring > yaml field (already on the profile).
    if env.token_key:
        profile.token_key = env.token_key
    elif not profile.token_key:
        profile.token_key = get_keyring_token(profile.name)

    if mock is not None:
        profile.mock = mock

    return profile

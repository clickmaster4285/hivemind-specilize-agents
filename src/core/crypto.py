"""Encryption at rest for secrets the app keeps (BACKLOG L16 S5): AES-256-GCM.

Used for device logins (Settings → Devices, `devices.secret_enc`). The key is `DEVICE_KEY` in `.env` only, never
in the database: a copy or backup of the database alone does not reveal a password. Whoever has both the
database and `.env` can decrypt — that is the limit of keeping the key on the same machine.

Each value is bound to where it belongs (`aad`, e.g. its owner and row): moved to another row, it no longer
decrypts. A token is "v1:" + base64url(nonce ‖ ciphertext ‖ tag), so a later format can sit beside this one.
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.core.config import Config

KEY_BYTES = 32            # AES-256
NONCE_BYTES = 12          # the GCM nonce size NIST SP 800-38D recommends; a fresh random one per value
VERSION = "v1"
NEW_KEY_HINT = 'python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"'


class CryptoError(RuntimeError):
    """No usable key, or a value that does not decrypt (wrong key, or moved / altered)."""


def _key(config: Config) -> bytes:
    try:
        key = base64.urlsafe_b64decode(config.device_key.encode() + b"=" * (-len(config.device_key) % 4))
    except (ValueError, TypeError) as exc:
        raise CryptoError(f"DEVICE_KEY in .env is not valid base64 — make one with: {NEW_KEY_HINT}") from exc
    if len(key) != KEY_BYTES:
        raise CryptoError(f"DEVICE_KEY in .env must be {KEY_BYTES} random bytes (base64) — make one with: {NEW_KEY_HINT}")
    return key


def check_key(config: Config) -> None:
    """At startup: refuse to run without a usable key, so no device can be saved unencrypted or unreadable."""
    _key(config)


def encrypt(text: str, aad: str, config: Config | None = None) -> str:
    nonce = os.urandom(NONCE_BYTES)
    sealed = AESGCM(_key(config or Config.from_env())).encrypt(nonce, text.encode(), aad.encode())
    return f"{VERSION}:" + base64.urlsafe_b64encode(nonce + sealed).decode()


def decrypt(token: str, aad: str, config: Config | None = None) -> str:
    version, _, body = token.partition(":")
    if version != VERSION:
        raise CryptoError(f"unknown encryption format '{version}'")
    raw = base64.urlsafe_b64decode(body.encode())
    try:
        return AESGCM(_key(config or Config.from_env())).decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:],
                                                                  aad.encode()).decode()
    except InvalidTag as exc:
        raise CryptoError("a saved login could not be decrypted — was DEVICE_KEY changed?") from exc

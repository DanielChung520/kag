"""Per-KB API key generation, hashing, and verification.

Format: ``kag_<32 base62 chars>`` (e.g. ``kag_aB3...9zX``).
Storage: only the SHA-256 of ``key + KAG_API_KEY_PEPPER`` is persisted.
The raw key is returned to the caller **once** at creation and cannot
be recovered from the stored hash.
"""

from __future__ import annotations

import hashlib
import secrets
import string

from kag.config import get_settings

KEY_PREFIX = "kag_"
RANDOM_LENGTH = 32
_BASE62_ALPHABET = string.ascii_letters + string.digits


def generate_api_key() -> str:
    """Return a fresh raw API key (e.g. ``kag_aB3...9zX``).

    Uses :func:`secrets.choice` over the base62 alphabet (a-zA-Z0-9);
    32 chars gives ~190 bits of entropy — more than enough to resist
    brute force on a SHA-256 hash.
    """
    body = "".join(secrets.choice(_BASE62_ALPHABET) for _ in range(RANDOM_LENGTH))
    return f"{KEY_PREFIX}{body}"


def hash_key(key: str) -> str:
    """SHA-256 of ``key + KAG_API_KEY_PEPPER``, lowercase hex.

    The pepper is a process-level secret loaded from settings. A leaked
    hash + a leaked pepper together still cannot recover the raw key
    (the hash is one-way) — but a leaked hash without the pepper is
    useless for offline brute force.
    """
    pepper = get_settings().KAG_API_KEY_PEPPER
    return hashlib.sha256(f"{key}{pepper}".encode()).hexdigest()


def verify_key(key: str, key_hash: str) -> bool:
    """Constant-time comparison of a raw key against a stored hash."""
    return secrets.compare_digest(hash_key(key), key_hash)

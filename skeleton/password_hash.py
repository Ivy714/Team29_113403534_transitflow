"""
Argon2id password hashing for TransitFlow credentials.

Stores (hash_hex, salt_bytes) in user_credentials / user_security_questions
to match schema columns password_hash TEXT and password_salt BYTEA.
"""

from __future__ import annotations

import os
import secrets

from argon2.low_level import Type, hash_secret_raw

# Course-recommended adaptive hashing; tuned for local dev (not production load).
_TIME_COST = 2
_MEMORY_COST = 65536
_PARALLELISM = 1
_HASH_LEN = 32
_SALT_LEN = 16


def hash_password(plaintext: str) -> tuple[str, bytes]:
    """Return (hex digest, salt) for storing in PostgreSQL."""
    salt = os.urandom(_SALT_LEN)
    digest = hash_secret_raw(
        secret=plaintext.encode("utf-8"),
        salt=salt,
        time_cost=_TIME_COST,
        memory_cost=_MEMORY_COST,
        parallelism=_PARALLELISM,
        hash_len=_HASH_LEN,
        type=Type.ID,
    )
    return digest.hex(), salt


def verify_password(plaintext: str, stored_hash: str, stored_salt: bytes) -> bool:
    """Verify plaintext against stored hex digest and salt."""
    try:
        digest = hash_secret_raw(
            secret=plaintext.encode("utf-8"),
            salt=stored_salt,
            time_cost=_TIME_COST,
            memory_cost=_MEMORY_COST,
            parallelism=_PARALLELISM,
            hash_len=_HASH_LEN,
            type=Type.ID,
        )
        return secrets.compare_digest(digest.hex(), stored_hash)
    except (ValueError, TypeError):
        return False

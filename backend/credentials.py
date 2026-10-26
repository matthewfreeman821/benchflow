"""Password primitives. Never normalize passwords or return credential hashes."""

from __future__ import annotations

import hashlib
import secrets
import unicodedata

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

from contracts.auth import ActivateRequest

_HASHER = PasswordHasher(
    time_cost=3, memory_cost=65536, parallelism=1, hash_len=32, salt_len=16, type=Type.ID
)


def normalize_login(login_id: str) -> str:
    # Use the shared validator, not a second divergent identifier grammar.
    ActivateRequest.model_validate({
        "code": "a" * 43, "login_id": login_id, "password": "x" * 12,
    })
    return unicodedata.normalize("NFKC", login_id).casefold()


def hash_password(password: str) -> str:
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError("PASSWORD_LENGTH")
    return _HASHER.hash(password)


def verify_password(encoded: str | None, password: str) -> bool:
    if encoded is None or not isinstance(password, str) or len(password) > 256:
        return False
    try:
        return _HASHER.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()

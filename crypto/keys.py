"""Key derivation and in-memory group key storage."""

from __future__ import annotations

import secrets
from collections.abc import Mapping, MutableMapping

from argon2.low_level import Type, hash_secret_raw
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from crypto.constants import AES_KEY_SIZE, HKDF_INFO_MSG, SALT_SIZE
from crypto.errors import CryptoError

# Argon2id defaults (design doc §11.3)
ARGON2_TIME_COST = 3
ARGON2_MEMORY_KIB = 65_536
ARGON2_PARALLELISM = 1


def generate_salt() -> bytes:
    """Return 16 random bytes for password-based key derivation."""
    return secrets.token_bytes(SALT_SIZE)


def derive_key_from_password(password: str, *, salt: bytes) -> bytes:
    """Derive a 32-byte AES key from a network password using Argon2id."""
    if not isinstance(password, str) or password == "":
        raise CryptoError("password must be a non-empty string")
    if len(salt) != SALT_SIZE:
        raise CryptoError(f"salt must be {SALT_SIZE} bytes")

    return hash_secret_raw(
        secret=password.encode("utf-8"),
        salt=salt,
        time_cost=ARGON2_TIME_COST,
        memory_cost=ARGON2_MEMORY_KIB,
        parallelism=ARGON2_PARALLELISM,
        hash_len=AES_KEY_SIZE,
        type=Type.ID,
    )


def derive_key_from_psk(psk: bytes, *, salt: bytes) -> bytes:
    """Derive a 32-byte AES key from a binary pre-shared secret using HKDF-SHA256."""
    if not isinstance(psk, bytes) or len(psk) == 0:
        raise CryptoError("psk must be non-empty bytes")
    if len(salt) == 0:
        raise CryptoError("salt must be non-empty bytes")

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=AES_KEY_SIZE,
        salt=salt,
        info=HKDF_INFO_MSG,
    )
    return hkdf.derive(psk)


def password_hasher_params() -> dict[str, int]:
    """Expose Argon2 parameters for discovery/UI hints."""
    return {
        "time_cost": ARGON2_TIME_COST,
        "memory_kib": ARGON2_MEMORY_KIB,
        "parallelism": ARGON2_PARALLELISM,
    }


class GroupKeyRing(MutableMapping[int, bytes]):
    """In-memory key_id -> group key mapping for decrypt and rotation."""

    def __init__(self, keys: Mapping[int, bytes] | None = None) -> None:
        self._keys: dict[int, bytes] = {}
        if keys:
            for key_id, key in keys.items():
                self[key_id] = key

    def __getitem__(self, key_id: int) -> bytes:
        return self._keys[key_id]

    def __setitem__(self, key_id: int, key: bytes) -> None:
        if not isinstance(key, bytes) or len(key) != AES_KEY_SIZE:
            raise CryptoError(f"group key must be {AES_KEY_SIZE} bytes")
        self._keys[int(key_id)] = bytes(key)

    def __delitem__(self, key_id: int) -> None:
        del self._keys[int(key_id)]

    def __iter__(self):
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def clear_keys(self) -> None:
        """Remove all keys from memory."""
        self._keys.clear()

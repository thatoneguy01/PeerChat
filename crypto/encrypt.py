"""AES-256-GCM encrypt/decrypt for PeerChat message payloads."""

from __future__ import annotations

import secrets
from collections.abc import Mapping

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from crypto.constants import (
    AES_KEY_SIZE,
    DEFAULT_ROOM_ID,
    ENVELOPE_VERSION,
    MAX_PLAINTEXT_BYTES,
    NONCE_SIZE,
)
from crypto.envelope import build_aad, pack_envelope, unpack_envelope
from crypto.errors import DecryptFailedError, PlaintextTooLargeError


def encrypt_message(
    *,
    key: bytes,
    key_id: int = 0,
    room_id: int = DEFAULT_ROOM_ID,
    plaintext: bytes,
) -> bytes:
    """
    Encrypt plaintext and return the full binary crypto envelope.

    Uses a random 12-byte GCM nonce per message. AESGCM output is
    ciphertext || 16-byte authentication tag (trailing).
    """
    if not isinstance(key, bytes) or len(key) != AES_KEY_SIZE:
        raise ValueError(f"key must be {AES_KEY_SIZE} bytes")
    if len(plaintext) > MAX_PLAINTEXT_BYTES:
        raise PlaintextTooLargeError(
            f"plaintext exceeds {MAX_PLAINTEXT_BYTES} bytes"
        )

    nonce = secrets.token_bytes(NONCE_SIZE)
    aad = build_aad(room_id=room_id, envelope_version=ENVELOPE_VERSION)
    ciphertext_with_tag = AESGCM(key).encrypt(nonce, plaintext, aad)
    return pack_envelope(
        key_id=key_id,
        nonce=nonce,
        ciphertext_with_tag=ciphertext_with_tag,
    )


def decrypt_message(
    *,
    keyring: Mapping[int, bytes],
    room_id: int = DEFAULT_ROOM_ID,
    envelope: bytes,
) -> bytes:
    """Decrypt envelope bytes; raises on tamper, wrong key, or version mismatch."""
    key_id, _version, nonce, ciphertext_with_tag = unpack_envelope(envelope)

    if key_id not in keyring:
        raise DecryptFailedError(f"no key for key_id={key_id}")

    key = keyring[key_id]
    if len(key) != AES_KEY_SIZE:
        raise DecryptFailedError("invalid key size in keyring")

    aad = build_aad(room_id=room_id, envelope_version=ENVELOPE_VERSION)
    try:
        return AESGCM(key).decrypt(nonce, ciphertext_with_tag, aad)
    except Exception as exc:
        raise DecryptFailedError("decryption failed") from exc

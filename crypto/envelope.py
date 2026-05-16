"""Build and parse the binary crypto envelope (v1)."""

from __future__ import annotations

import struct

from crypto.constants import (
    CIPHER_SUITE_AES_256_GCM,
    ENVELOPE_VERSION,
    FLAG_KEY_ID_PRESENT,
    GCM_TAG_SIZE,
    HEADER_SIZE,
    MAGIC,
    MAX_ENVELOPE_BYTES,
    NONCE_SIZE,
)
from crypto.errors import (
    InvalidEnvelopeError,
    UnknownSuiteError,
    UnsupportedEnvelopeVersionError,
)


def build_aad(*, room_id: int, envelope_version: int = ENVELOPE_VERSION) -> bytes:
    """Associated authenticated data: peerchat\\0 || room_id || envelope_version."""
    from crypto.constants import AAD_PREFIX

    return AAD_PREFIX + struct.pack(">II", room_id & 0xFFFFFFFF, envelope_version & 0xFFFFFFFF)


def pack_envelope(
    *,
    key_id: int,
    nonce: bytes,
    ciphertext_with_tag: bytes,
) -> bytes:
    """
    Pack header + ciphertext||tag.

    cryptography AESGCM.encrypt returns ciphertext with a trailing 16-byte GCM tag.
    """
    if len(nonce) != NONCE_SIZE:
        raise InvalidEnvelopeError("nonce must be 12 bytes")
    if len(ciphertext_with_tag) < GCM_TAG_SIZE:
        raise InvalidEnvelopeError("ciphertext too short for GCM tag")

    flags = FLAG_KEY_ID_PRESENT if key_id != 0 else 0
    header = struct.pack(
        ">BBBBI",
        MAGIC,
        ENVELOPE_VERSION,
        CIPHER_SUITE_AES_256_GCM,
        flags,
        key_id & 0xFFFFFFFF,
    )
    envelope = header + nonce + ciphertext_with_tag
    if len(envelope) > MAX_ENVELOPE_BYTES:
        raise InvalidEnvelopeError("envelope exceeds maximum size")
    return envelope


def unpack_envelope(envelope: bytes) -> tuple[int, int, bytes, bytes]:
    """
    Parse envelope bytes.

    Returns (key_id, envelope_version, nonce, ciphertext_with_tag).
    """
    if len(envelope) > MAX_ENVELOPE_BYTES:
        raise InvalidEnvelopeError("envelope exceeds maximum size")
    if len(envelope) < HEADER_SIZE + GCM_TAG_SIZE:
        raise InvalidEnvelopeError("envelope too short")

    magic, version, suite, flags, key_id = struct.unpack(">BBBBI", envelope[:8])
    nonce = envelope[8:20]
    ciphertext_with_tag = envelope[20:]

    if magic != MAGIC:
        raise InvalidEnvelopeError(f"bad magic byte: {magic:#04x}")
    if version != ENVELOPE_VERSION:
        raise UnsupportedEnvelopeVersionError(version)
    if suite != CIPHER_SUITE_AES_256_GCM:
        raise UnknownSuiteError(suite)
    if flags & ~FLAG_KEY_ID_PRESENT:
        raise InvalidEnvelopeError(f"unsupported flags: {flags:#04x}")
    if (flags & FLAG_KEY_ID_PRESENT) == 0 and key_id != 0:
        raise InvalidEnvelopeError("key_id present without flag")

    return key_id, version, nonce, ciphertext_with_tag

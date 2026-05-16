"""Helpers to embed crypto envelopes in distribution Message.content."""

from __future__ import annotations

import base64

from crypto.encrypt import decrypt_message, encrypt_message

# Prefix so receivers can distinguish encrypted payloads from legacy cleartext.
ENVELOPE_PREFIX = "pc1:"


def encrypt_content(
    *,
    key: bytes,
    key_id: int = 0,
    room_id: int = 0,
    plaintext: str,
) -> str:
    """Encrypt UTF-8 chat text; return a prefixed base64url-safe wire string."""
    envelope = encrypt_message(
        key=key,
        key_id=key_id,
        room_id=room_id,
        plaintext=plaintext.encode("utf-8"),
    )
    encoded = base64.urlsafe_b64encode(envelope).decode("ascii").rstrip("=")
    return f"{ENVELOPE_PREFIX}{encoded}"


def decrypt_content(
    *,
    keyring,
    room_id: int = 0,
    content: str,
) -> str:
    """Decrypt content produced by encrypt_content; pass through legacy cleartext."""
    if not content.startswith(ENVELOPE_PREFIX):
        return content
    padded = content[len(ENVELOPE_PREFIX) :]
    pad = "=" * (-len(padded) % 4)
    envelope = base64.urlsafe_b64decode(padded + pad)
    plaintext = decrypt_message(keyring=keyring, room_id=room_id, envelope=envelope)
    return plaintext.decode("utf-8")


def is_encrypted_content(content: str) -> bool:
    return content.startswith(ENVELOPE_PREFIX)

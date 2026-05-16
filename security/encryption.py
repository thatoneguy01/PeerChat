"""
Per-recipient message encryption (RSA-OAEP + AES-256-GCM hybrid).

Aligns with team direction (Ryan / Himanshu / Brandon):
- Each peer has an RSA keypair; private key in Brandon's InMemoryKeyStore.
- Peer Discovery distributes PEM public keys.
- Chat: one hybrid ciphertext per recipient; broadcast carries all boxes in content.
"""

from __future__ import annotations

import base64
import json
import secrets
import struct
from collections.abc import Mapping

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

from crypto.constants import AES_KEY_SIZE
from crypto.encrypt import decrypt_message as decrypt_aes_envelope
from crypto.encrypt import encrypt_message
from crypto.errors import DecryptFailedError
from security.rsa_keys import load_private_key, load_public_key, public_key_pem_from_private

# JSON wire version stored in Message.content
WIRE_VERSION = "pcrsa-h1"
LEGACY_GROUP_PREFIX = "pc1:"

# Binary per-recipient blob: PC\x02\x01 | u16 wrapped_len | RSA(wrapped_aes_key) | aes_envelope
_HYBRID_MAGIC = b"PC\x02\x01"
_MAX_RSA_PLAINTEXT = 190  # RSA-2048 OAEP-SHA256 practical limit for "rsa-only" mode


class EncryptionError(Exception):
    """Base exception for payload encryption."""


class DecryptPayloadError(EncryptionError):
    """Cannot decrypt (wrong key, tamper, unknown format)."""


def _oaep_encrypt(public_key: RSAPublicKey, data: bytes) -> bytes:
    return public_key.encrypt(
        data,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )


def _oaep_decrypt(private_key: RSAPrivateKey, data: bytes) -> bytes:
    return private_key.decrypt(
        data,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )


def encrypt_for_peer(*, plaintext: bytes, recipient_public_key_pem: bytes) -> bytes:
    """
    Hybrid encrypt for one recipient.

    RSA-OAEP wraps a random 32-byte AES key; AES-GCM encrypts the payload
    (crypto.encrypt_message). Returns an opaque binary blob.
    """
    public_key = load_public_key(recipient_public_key_pem)
    raw_aes_key = secrets.token_bytes(AES_KEY_SIZE)
    aes_envelope = encrypt_message(key=raw_aes_key, plaintext=plaintext)
    wrapped_key = _oaep_encrypt(public_key, raw_aes_key)
    return _pack_hybrid(wrapped_key, aes_envelope)


def _pack_hybrid(wrapped_key: bytes, aes_envelope: bytes) -> bytes:
    if len(wrapped_key) > 0xFFFF:
        raise EncryptionError("wrapped key too large")
    return _HYBRID_MAGIC + struct.pack(">H", len(wrapped_key)) + wrapped_key + aes_envelope


def _unpack_hybrid(blob: bytes) -> tuple[bytes, bytes]:
    if len(blob) < len(_HYBRID_MAGIC) + 2:
        raise DecryptPayloadError("hybrid blob too short")
    if not blob.startswith(_HYBRID_MAGIC):
        raise DecryptPayloadError("bad hybrid magic")
    (wrapped_len,) = struct.unpack(">H", blob[len(_HYBRID_MAGIC) : len(_HYBRID_MAGIC) + 2])
    start = len(_HYBRID_MAGIC) + 2
    end = start + wrapped_len
    if end > len(blob):
        raise DecryptPayloadError("truncated hybrid blob")
    return blob[start:end], blob[end:]


def decrypt_from_peer(*, ciphertext: bytes, private_key_pem: bytes) -> bytes:
    """Decrypt a hybrid blob encrypted to this peer's public key."""
    private_key = load_private_key(private_key_pem)
    try:
        wrapped_key, aes_envelope = _unpack_hybrid(ciphertext)
        raw_aes_key = _oaep_decrypt(private_key, wrapped_key)
        return decrypt_aes_envelope(keyring={0: raw_aes_key}, envelope=aes_envelope)
    except DecryptFailedError as exc:
        raise DecryptPayloadError("AES layer decrypt failed") from exc
    except Exception as exc:
        raise DecryptPayloadError("RSA hybrid decrypt failed") from exc


def encrypt_for_peer_rsa_only(*, plaintext: bytes, recipient_public_key_pem: bytes) -> bytes:
    """RSA-OAEP only (short payloads). For chat, prefer hybrid encrypt_for_peer."""
    if len(plaintext) > _MAX_RSA_PLAINTEXT:
        raise EncryptionError(
            f"plaintext exceeds RSA-only limit ({_MAX_RSA_PLAINTEXT} bytes); use hybrid"
        )
    public_key = load_public_key(recipient_public_key_pem)
    return _oaep_encrypt(public_key, plaintext)


def decrypt_from_peer_rsa_only(*, ciphertext: bytes, private_key_pem: bytes) -> bytes:
    private_key = load_private_key(private_key_pem)
    try:
        return _oaep_decrypt(private_key, ciphertext)
    except Exception as exc:
        raise DecryptPayloadError("RSA-only decrypt failed") from exc


def get_public_key_pem(private_key_pem: bytes) -> bytes:
    """Expose this node's public key PEM for Peer Discovery."""
    return public_key_pem_from_private(private_key_pem)


def encrypt_broadcast_content(
    *,
    plaintext: str,
    recipient_pubkeys: Mapping[str, bytes],
) -> str:
    """
    Build Message.content for a room broadcast.

    JSON: {"v":"pcrsa-h1","boxes":{"user_id":"<urlsafe-b64 hybrid blob>", ...}}.
    Each member decrypts the entry for their user_id.
    """
    if not recipient_pubkeys:
        raise EncryptionError("no recipients")

    boxes: dict[str, str] = {}
    body = plaintext.encode("utf-8")
    for user_id, pub_pem in recipient_pubkeys.items():
        blob = encrypt_for_peer(plaintext=body, recipient_public_key_pem=pub_pem)
        boxes[user_id] = base64.urlsafe_b64encode(blob).decode("ascii").rstrip("=")

    return json.dumps({"v": WIRE_VERSION, "boxes": boxes}, separators=(",", ":"))


def decrypt_broadcast_content(
    *,
    content: str,
    own_user_id: str,
    private_key_pem: bytes,
) -> str:
    """Decrypt this peer's box from a broadcast Message.content."""
    if not content or content.startswith(LEGACY_GROUP_PREFIX):
        return content

    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return content

    if payload.get("v") != WIRE_VERSION:
        raise DecryptPayloadError(f"unsupported wire version: {payload.get('v')}")

    boxes = payload.get("boxes")
    if not isinstance(boxes, dict) or own_user_id not in boxes:
        raise DecryptPayloadError(f"no ciphertext for user_id={own_user_id}")

    padded = boxes[own_user_id]
    pad = "=" * (-len(padded) % 4)
    blob = base64.urlsafe_b64decode(padded + pad)
    plaintext = decrypt_from_peer(ciphertext=blob, private_key_pem=private_key_pem)
    return plaintext.decode("utf-8")


def is_encrypted_content(content: str) -> bool:
    if content.startswith(LEGACY_GROUP_PREFIX):
        return True
    try:
        payload = json.loads(content)
        return payload.get("v") == WIRE_VERSION
    except json.JSONDecodeError:
        return False

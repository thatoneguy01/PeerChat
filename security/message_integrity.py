from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from distribution import Message
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey


SIGNATURE_VERSION = "v1"
SIGNATURE_ALGORITHM = "rsa-pss-sha256"

_private_key: RSAPrivateKey | None = None
_public_keys: dict[str, RSAPublicKey] = {}


class MessageIntegrityError(Exception):
    """Raised when message integrity signing cannot be performed."""


def generate_private_key() -> RSAPrivateKey:
    """Generate an RSA private key for this peer."""
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def configure_private_key(private_key: RSAPrivateKey | bytes) -> None:
    """Load this peer's RSA private key for signing outgoing messages."""
    global _private_key
    _private_key = _coerce_private_key(private_key)


def get_public_key_pem() -> bytes:
    """Return this peer's public key in PEM form for discovery/roster sharing."""
    if _private_key is None:
        raise MessageIntegrityError("No private key configured")
    return _private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def register_public_key(sender: str, public_key: RSAPublicKey | bytes) -> None:
    """Register a sender's RSA public key for verifying incoming messages."""
    _public_keys[sender] = _coerce_public_key(public_key)


def clear_keys() -> None:
    """Clear loaded signing and verification keys. Useful for tests/shutdown."""
    global _private_key
    _private_key = None
    _public_keys.clear()


def canonical_message_payload(msg: Message) -> bytes:
    """
    Build the canonical payload covered by the message signature.

    This follows the current Distribution contract: sign only stable fields and
    exclude ttl and vector_clock because Distribution mutates them in transit.
    """
    payload: dict[str, Any] = {
        "id": msg.id,
        "sender": msg.sender,
        "timestamp": msg.timestamp,
        "content": msg.content,
        "signature": "",
    }
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def sign(msg: Message) -> Message:
    """Fill msg.signature and return the same message for chaining."""
    if _private_key is None:
        raise MessageIntegrityError("No private key configured")

    signature = _private_key.sign(
        canonical_message_payload(msg),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    encoded = base64.b64encode(signature).decode("ascii")
    msg.signature = f"{SIGNATURE_VERSION}:{SIGNATURE_ALGORITHM}:{encoded}"
    return msg


def verify(msg: Message) -> bool:
    """Return True when msg.signature is valid for the sender's public key."""
    try:
        version, algorithm, encoded_signature = msg.signature.split(":", 2)
        if version != SIGNATURE_VERSION or algorithm != SIGNATURE_ALGORITHM:
            return False

        public_key = _public_keys.get(msg.sender)
        if public_key is None:
            return False

        signature = base64.b64decode(encoded_signature.encode("ascii"), validate=True)
        public_key.verify(
            signature,
            canonical_message_payload(msg),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return True
    except (
        AttributeError,
        InvalidSignature,
        TypeError,
        ValueError,
        binascii.Error,
    ):
        return False


def _coerce_private_key(private_key: RSAPrivateKey | bytes) -> RSAPrivateKey:
    if isinstance(private_key, bytes):
        loaded = serialization.load_pem_private_key(private_key, password=None)
        if not isinstance(loaded, RSAPrivateKey):
            raise MessageIntegrityError("Expected an RSA private key")
        return loaded

    if isinstance(private_key, RSAPrivateKey):
        return private_key

    raise MessageIntegrityError("Expected an RSA private key or PEM bytes")


def _coerce_public_key(public_key: RSAPublicKey | bytes) -> RSAPublicKey:
    if isinstance(public_key, bytes):
        loaded = serialization.load_pem_public_key(public_key)
        if not isinstance(loaded, RSAPublicKey):
            raise MessageIntegrityError("Expected an RSA public key")
        return loaded

    if isinstance(public_key, RSAPublicKey):
        return public_key

    raise MessageIntegrityError("Expected an RSA public key or PEM bytes")

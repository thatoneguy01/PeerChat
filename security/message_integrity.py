from __future__ import annotations

import base64
import binascii
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from distribution import Message


_private_key = None
_public_keys = {}


def configure_private_key(private_key_pem: bytes) -> None:
    """Load Brandon's stored RSA private key PEM for outgoing signatures."""
    global _private_key
    _private_key = serialization.load_pem_private_key(private_key_pem, password=None)


def register_public_key(sender: str, public_key_pem: bytes) -> None:
    """Register a sender public key from the peer registry for verification."""
    _public_keys[sender] = serialization.load_pem_public_key(public_key_pem)


def clear_keys() -> None:
    """Reset configured keys. Used by tests and clean shutdown paths."""
    global _private_key
    _private_key = None
    _public_keys.clear()


def sign(msg: Message) -> Message:
    """Fill msg.signature over agreed stable fields and return msg."""
    if _private_key is None:
        raise RuntimeError("private key is not configured")

    signature = _private_key.sign(
        _canonical_payload(msg),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    msg.signature = base64.b64encode(signature).decode("ascii")
    return msg


def verify(msg: Message) -> bool:
    """Return True when msg.signature verifies with msg.sender's public key."""
    public_key = _public_keys.get(msg.sender)
    if public_key is None or not msg.signature:
        return False

    try:
        public_key.verify(
            base64.b64decode(msg.signature.encode("ascii"), validate=True),
            _canonical_payload(msg),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return True
    except (InvalidSignature, TypeError, ValueError, binascii.Error):
        return False


def _canonical_payload(msg: Message) -> bytes:
    payload = {
        "id": msg.id,
        "sender": msg.sender,
        "timestamp": msg.timestamp,
        "content": msg.content,
        "signature": "",
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

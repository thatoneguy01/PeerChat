"""
RSA message signatures (integrity / authorship).

Signs stable fields only per docs/contract_security.md:
id, sender, timestamp, content (signature empty in canonical form).
Does not sign ttl or vector_clock.
"""

from __future__ import annotations

import base64
import json

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

from distribution.message import Message
from security.rsa_keys import load_private_key, load_public_key


def canonical_sign_bytes(msg: Message) -> bytes:
    payload = {
        "id": msg.id,
        "sender": msg.sender,
        "timestamp": msg.timestamp,
        "content": msg.content,
        "signature": "",
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_message(msg: Message, private_key_pem: bytes) -> Message:
    private_key = load_private_key(private_key_pem)
    signature = private_key.sign(
        canonical_sign_bytes(msg),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.MAX_LENGTH,
        ),
        hashes.SHA256(),
    )
    msg.signature = base64.b64encode(signature).decode("ascii")
    return msg


def verify_message(msg: Message, public_key_pem: bytes) -> bool:
    if not msg.signature:
        return False
    try:
        public_key = load_public_key(public_key_pem)
        signature = base64.b64decode(msg.signature.encode("ascii"))
        public_key.verify(
            signature,
            canonical_sign_bytes(msg),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )
        return True
    except Exception:
        return False

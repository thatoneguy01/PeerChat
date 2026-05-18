"""
Chat payload encryption for Message.content (hybrid RSA-OAEP + AES-256-GCM).

Wire format (JSON string in content):
  {"v": "pcenc-h1", "boxes": {"host:port": "<base64 hybrid blob>", ...}}
"""

from __future__ import annotations

import base64
import json
import os
from typing import Mapping

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from distribution import Message

WIRE_VERSION = "pcenc-h1"


class PayloadEncryptionError(Exception):
    pass


def is_encrypted_content(content: str) -> bool:
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(data, dict) and data.get("v") == WIRE_VERSION


def encrypt_payload(
    msg: Message,
    recipient_pubkeys: Mapping[str, bytes],
    *,
    own_user_id: str,
) -> Message:
    """
    Replace msg.content with per-recipient hybrid ciphertext boxes.

    If no recipient keys are available, content is left unchanged (plaintext).
    """
    pubkeys = dict(recipient_pubkeys)
    if own_user_id and own_user_id not in pubkeys:
        pass  # caller should include self when desired

    valid = {uid: pem for uid, pem in pubkeys.items() if pem}
    if not valid:
        return msg

    plaintext = msg.content.encode("utf-8")
    boxes: dict[str, str] = {}
    for user_id, public_key_pem in valid.items():
        boxes[user_id] = base64.b64encode(
            _hybrid_encrypt(plaintext, public_key_pem)
        ).decode("ascii")

    msg.content = json.dumps({"v": WIRE_VERSION, "boxes": boxes}, separators=(",", ":"))
    return msg


def decrypt_payload(msg: Message, own_user_id: str, private_key_pem: bytes) -> Message:
    """
    Decrypt msg.content for own_user_id when wire format is pcenc-h1.

    Plaintext content is returned unchanged. Raises PayloadEncryptionError on
    corrupt wire data or missing box when encryption was expected.
    """
    if not is_encrypted_content(msg.content):
        return msg

    try:
        data = json.loads(msg.content)
        boxes = data.get("boxes") or {}
        encoded = boxes.get(own_user_id)
        if not encoded:
            raise PayloadEncryptionError(f"no ciphertext box for {own_user_id!r}")
        ciphertext = base64.b64decode(encoded.encode("ascii"), validate=True)
        msg.content = _hybrid_decrypt(ciphertext, private_key_pem).decode("utf-8")
        return msg
    except PayloadEncryptionError:
        raise
    except Exception as exc:
        raise PayloadEncryptionError("failed to decrypt payload") from exc


def _hybrid_encrypt(plaintext: bytes, public_key_pem: bytes) -> bytes:
    public_key = serialization.load_pem_public_key(public_key_pem)
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise PayloadEncryptionError("public key must be RSA")

    aes_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    ciphertext = AESGCM(aes_key).encrypt(nonce, plaintext, None)
    key_material = aes_key + nonce
    encrypted_key_material = public_key.encrypt(
        key_material,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    rsa_len = len(encrypted_key_material)
    return rsa_len.to_bytes(2, "big") + encrypted_key_material + ciphertext


def _hybrid_decrypt(ciphertext: bytes, private_key_pem: bytes) -> bytes:
    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise PayloadEncryptionError("private key must be RSA")
    if len(ciphertext) < 2:
        raise PayloadEncryptionError("ciphertext too short")

    rsa_len = int.from_bytes(ciphertext[:2], "big")
    if len(ciphertext) < 2 + rsa_len:
        raise PayloadEncryptionError("ciphertext truncated")

    encrypted_key_material = ciphertext[2 : 2 + rsa_len]
    aes_ciphertext = ciphertext[2 + rsa_len :]

    key_material = private_key.decrypt(
        encrypted_key_material,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    if len(key_material) != 44:
        raise PayloadEncryptionError("invalid key material length")

    aes_key = key_material[:32]
    nonce = key_material[32:]
    return AESGCM(aes_key).decrypt(nonce, aes_ciphertext, None)

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from distribution import Message
from security.payload_encryption import (
    WIRE_VERSION,
    PayloadEncryptionError,
    decrypt_payload,
    encrypt_payload,
    is_encrypted_content,
)

ALICE = "127.0.0.1:5001"
BOB = "127.0.0.1:5002"


@pytest.fixture
def alice_keys():
    return _generate_keypair()


@pytest.fixture
def bob_keys():
    return _generate_keypair()


def _generate_keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def test_encrypt_decrypt_round_trip(alice_keys, bob_keys):
    alice_priv, alice_pub = alice_keys
    bob_priv, bob_pub = bob_keys

    msg = Message(content="hello room", sender=ALICE)
    wire = encrypt_payload(
        msg,
        {ALICE: alice_pub, BOB: bob_pub},
        own_user_id=ALICE,
    )

    assert is_encrypted_content(wire.content)
    payload = json.loads(wire.content)
    assert payload["v"] == WIRE_VERSION
    assert ALICE in payload["boxes"]
    assert BOB in payload["boxes"]

    for user_id, private_pem in ((ALICE, alice_priv), (BOB, bob_priv)):
        copy = Message(
            content=wire.content,
            sender=wire.sender,
            id=wire.id,
            timestamp=wire.timestamp,
        )
        decrypted = decrypt_payload(copy, user_id, private_pem)
        assert decrypted.content == "hello room"


def test_plaintext_content_passes_through_decrypt(alice_keys):
    alice_priv, _ = alice_keys
    msg = Message(content="still plain", sender=ALICE)
    out = decrypt_payload(msg, ALICE, alice_priv)
    assert out.content == "still plain"
    assert not is_encrypted_content(out.content)


def test_encrypt_without_recipients_leaves_plaintext():
    msg = Message(content="solo", sender=ALICE)
    out = encrypt_payload(msg, {}, own_user_id=ALICE)
    assert out.content == "solo"


def test_decrypt_missing_box_raises(alice_keys, bob_keys):
    _, bob_pub = bob_keys
    alice_priv, alice_pub = alice_keys

    msg = Message(content="secret", sender=ALICE)
    wire = encrypt_payload(msg, {BOB: bob_pub}, own_user_id=ALICE)

    with pytest.raises(PayloadEncryptionError, match="no ciphertext box"):
        decrypt_payload(wire, ALICE, alice_priv)


def test_wrong_private_key_fails(alice_keys, bob_keys):
    _, bob_pub = bob_keys
    alice_priv, alice_pub = alice_keys

    msg = Message(content="secret", sender=ALICE)
    wire = encrypt_payload(msg, {ALICE: alice_pub, BOB: bob_pub}, own_user_id=ALICE)

    with pytest.raises(PayloadEncryptionError):
        decrypt_payload(wire, BOB, alice_priv)

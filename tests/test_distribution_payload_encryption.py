"""Payload encryption in BroadcastNode (encrypt before sign, decrypt for UI)."""

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from distribution import BroadcastNode, InMemoryRegistry, Message
from security import clear_keys, configure_private_key
from security.payload_encryption import encrypt_payload, is_encrypted_content

NODE_A = "127.0.0.1:5001"
NODE_B = "127.0.0.1:5002"


@pytest.fixture(autouse=True)
def reset_security_keys():
    clear_keys()
    yield
    clear_keys()


@pytest.fixture
def keypairs():
    out = {}
    for node in (NODE_A, NODE_B):
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
        out[node] = (private_pem, public_pem)
    return out


def test_encrypt_outgoing_builds_per_recipient_boxes(keypairs):
    _, alice_pub = keypairs[NODE_A]
    _, bob_pub = keypairs[NODE_B]

    registry = InMemoryRegistry()
    registry.add_peer("127.0.0.1", 5001, alice_pub.decode())
    registry.add_peer("127.0.0.1", 5002, bob_pub.decode())

    node = BroadcastNode("127.0.0.1", 5001, registry)
    node.own_public_key_pem = alice_pub
    msg = Message(content="hello", sender=NODE_A)

    assert node._encrypt_outgoing(msg) is True
    assert is_encrypted_content(msg.content)


def test_encrypt_outgoing_skips_already_encrypted_content(keypairs):
    _, alice_pub = keypairs[NODE_A]
    _, bob_pub = keypairs[NODE_B]

    registry = InMemoryRegistry()
    registry.add_peer("127.0.0.1", 5001, alice_pub.decode())
    registry.add_peer("127.0.0.1", 5002, bob_pub.decode())

    node = BroadcastNode("127.0.0.1", 5001, registry)
    node.own_public_key_pem = alice_pub
    msg = Message(content='{"v":"pcenc-h1","boxes":{}}', sender=NODE_A)

    assert node._encrypt_outgoing(msg) is True
    assert msg.content == '{"v":"pcenc-h1","boxes":{}}'


def test_decrypt_for_display_decrypts_in_place(keypairs):
    alice_priv, alice_pub = keypairs[NODE_A]
    _, bob_pub = keypairs[NODE_B]

    configure_private_key(alice_priv)
    registry = InMemoryRegistry()
    node = BroadcastNode("127.0.0.1", 5001, registry)

    wire = Message(content="secret", sender=NODE_B)
    encrypt_payload(
        wire,
        {NODE_A: alice_pub, NODE_B: bob_pub},
        own_user_id=NODE_B,
    )
    assert is_encrypted_content(wire.content)

    node.decrypt_for_display(wire)
    assert wire.content == "secret"

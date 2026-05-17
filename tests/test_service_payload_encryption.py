"""UI service: plaintext send, decrypt-on-receive."""

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from distribution import Message
from distribution.peer_registry import InMemoryRegistry
from security import clear_keys, configure_private_key, register_public_key, sign, verify
from security.key_storage import InMemoryKeyStore
from security.payload_encryption import encrypt_payload, is_encrypted_content
from ui.services.service import Service

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


def _make_service() -> Service:
    return Service(refreshes={"messages": lambda _: None, "users": lambda _: None})


def test_post_message_passes_plaintext_to_distribution(keypairs):
    alice_priv, _ = keypairs[NODE_A]

    sent: list[Message] = []
    service = _make_service()
    service.node_address = NODE_A
    service.key_store = InMemoryKeyStore()
    service.key_store.set_private_key(alice_priv)
    service.message_out = sent.append

    service.post_message("team update")

    assert len(sent) == 1
    assert sent[0].content == "team update"
    assert not is_encrypted_content(sent[0].content)


def test_message_received_decrypts_for_display(keypairs):
    alice_priv, alice_pub = keypairs[NODE_A]
    bob_priv, bob_pub = keypairs[NODE_B]

    configure_private_key(bob_priv)
    register_public_key(NODE_B, bob_pub)

    registry = InMemoryRegistry()
    registry.add_peer("127.0.0.1", 5001, alice_pub.decode())
    registry.add_peer("127.0.0.1", 5002, bob_pub.decode())

    wire_msg = Message(content="hello alice", sender=NODE_B)
    encrypt_payload(
        wire_msg,
        {NODE_A: alice_pub, NODE_B: bob_pub},
        own_user_id=NODE_B,
    )
    wire = sign(wire_msg)

    receiver = _make_service()
    receiver.node_address = NODE_A
    receiver.key_store = InMemoryKeyStore()
    receiver.key_store.set_private_key(alice_priv)
    receiver.history_service = None
    receiver.message_received(wire)

    assert receiver.get_messages()[-1]["content"] == "hello alice"


def test_sign_verify_survives_encrypted_content(keypairs):
    alice_priv, alice_pub = keypairs[NODE_A]
    _, bob_pub = keypairs[NODE_B]

    configure_private_key(alice_priv)
    register_public_key(NODE_A, alice_pub)

    msg = Message(content="signed ciphertext", sender=NODE_A)
    encrypt_payload(
        msg,
        {NODE_A: alice_pub, NODE_B: bob_pub},
        own_user_id=NODE_A,
    )
    signed = sign(msg)
    assert verify(signed)

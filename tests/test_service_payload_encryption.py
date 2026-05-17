"""UI service encrypt-on-send / decrypt-on-receive hooks."""

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from distribution import Message
from distribution.peer_registry import InMemoryRegistry
from security import clear_keys, configure_private_key, register_public_key, sign, verify
from security.key_storage import InMemoryKeyStore
from security.payload_encryption import is_encrypted_content
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


def test_post_message_encrypts_before_broadcast(keypairs):
    alice_priv, alice_pub = keypairs[NODE_A]
    _, bob_pub = keypairs[NODE_B]

    registry = InMemoryRegistry()
    registry.add_peer("127.0.0.1", 5001, alice_pub.decode())
    registry.add_peer("127.0.0.1", 5002, bob_pub.decode())

    sent: list[Message] = []
    service = _make_service()
    service.node_address = NODE_A
    service.key_store = InMemoryKeyStore()
    service.key_store.set_private_key(alice_priv)
    service.peer_registry = registry
    service.message_out = sent.append

    service.post_message("team update")

    assert len(sent) == 1
    assert is_encrypted_content(sent[0].content)
    payload = json.loads(sent[0].content)
    assert NODE_A in payload["boxes"]
    assert NODE_B in payload["boxes"]


def test_message_received_decrypts_for_display(keypairs):
    alice_priv, alice_pub = keypairs[NODE_A]
    bob_priv, bob_pub = keypairs[NODE_B]

    configure_private_key(bob_priv)
    register_public_key(NODE_B, bob_pub)

    registry = InMemoryRegistry()
    registry.add_peer("127.0.0.1", 5001, alice_pub.decode())
    registry.add_peer("127.0.0.1", 5002, bob_pub.decode())

    sender_service = _make_service()
    sender_service.node_address = NODE_B
    sender_service.key_store = InMemoryKeyStore()
    sender_service.key_store.set_private_key(bob_priv)
    sender_service.peer_registry = registry
    outbound: list[Message] = []
    sender_service.message_out = outbound.append
    sender_service.post_message("hello alice")

    wire = sign(outbound[0])

    receiver = _make_service()
    receiver.node_address = NODE_A
    receiver.key_store = InMemoryKeyStore()
    receiver.key_store.set_private_key(alice_priv)
    receiver.peer_registry = registry
    receiver.history_service = None
    receiver.message_received(wire)

    assert receiver.get_messages()[-1]["content"] == "hello alice"


def test_sign_verify_survives_encrypted_content(keypairs):
    alice_priv, alice_pub = keypairs[NODE_A]
    bob_priv, bob_pub = keypairs[NODE_B]

    configure_private_key(alice_priv)
    register_public_key(NODE_A, alice_pub)

    registry = InMemoryRegistry()
    registry.add_peer("127.0.0.1", 5002, bob_pub.decode())

    service = _make_service()
    service.node_address = NODE_A
    service.key_store = InMemoryKeyStore()
    service.key_store.set_private_key(alice_priv)
    service.peer_registry = registry
    outbound: list[Message] = []
    service.message_out = outbound.append
    service.post_message("signed ciphertext")

    msg = sign(outbound[0])
    assert verify(msg)

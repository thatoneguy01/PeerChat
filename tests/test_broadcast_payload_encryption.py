"""Encrypt (UI) → sign + deliver (BroadcastNode) → decrypt (UI)."""

import asyncio
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from distribution import BroadcastNode, InMemoryRegistry, Message
from security import clear_keys, configure_private_key, register_public_key, sign
from security.key_storage import InMemoryKeyStore
from security.payload_encryption import is_encrypted_content
from ui.services.service import Service

NODE_A = "127.0.0.1:5001"
NODE_B = "127.0.0.1:5002"


class FakeWebSocket:
    def __init__(self, raw_messages):
        self._messages = list(raw_messages)
        self.sent = []

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)

    async def send(self, raw):
        self.sent.append(json.loads(raw))


@pytest.fixture(autouse=True)
def reset_security_keys():
    clear_keys()
    yield
    clear_keys()


def _keypair():
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


def run(coro):
    return asyncio.run(coro)


async def _deliver_one(node, message):
    ws = FakeWebSocket([message.to_json()])
    await node._handle_ws(ws)
    return ws.sent


def test_encrypted_signed_message_decrypts_at_receiver():
    alice_priv, alice_pub = _keypair()
    bob_priv, bob_pub = _keypair()

    configure_private_key(alice_priv)
    register_public_key(NODE_A, alice_pub)
    register_public_key(NODE_B, bob_pub)

    registry = InMemoryRegistry()
    registry.add_peer("127.0.0.1", 5001, alice_pub.decode())
    registry.add_peer("127.0.0.1", 5002, bob_pub.decode())

    sender = Service(refreshes={})
    sender.node_address = NODE_A
    sender.key_store = InMemoryKeyStore()
    sender.key_store.set_private_key(alice_priv)
    sender.peer_registry = registry
    outbound: list[Message] = []
    sender.message_out = outbound.append
    sender.post_message("secure ping")

    wire = sign(outbound[0])
    assert is_encrypted_content(wire.content)

    receiver_node = BroadcastNode("127.0.0.1", 5002, registry, enforce_signatures=True)
    delivered: list[Message] = []
    receiver_node.on_message = delivered.append

    responses = run(_deliver_one(receiver_node, wire))
    assert responses == [{"ack": wire.id}]
    assert len(delivered) == 1

    display = Service(refreshes={"messages": lambda _msgs: None})
    display.node_address = NODE_B
    display.key_store = InMemoryKeyStore()
    display.key_store.set_private_key(bob_priv)
    display.history_service = None
    display.message_received(delivered[0])

    assert display.get_messages()[0]["content"] == "secure ping"

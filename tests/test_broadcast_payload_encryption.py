"""Distribution encrypt → sign → deliver with decrypt before UI callback."""

import asyncio
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from distribution import BroadcastNode, InMemoryRegistry, Message
from security import clear_keys, configure_private_key, register_public_key
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


def test_local_broadcast_decrypts_for_sender_ui():
    alice_priv, alice_pub = _keypair()
    _, bob_pub = _keypair()

    configure_private_key(alice_priv)
    register_public_key(NODE_A, alice_pub)
    register_public_key(NODE_B, bob_pub)

    registry = InMemoryRegistry()
    registry.add_peer("127.0.0.1", 5001, alice_pub.decode())
    registry.add_peer("127.0.0.1", 5002, bob_pub.decode())

    sender_node = BroadcastNode("127.0.0.1", 5001, registry, enforce_signatures=True)
    sender_node.own_public_key_pem = alice_pub
    originated: list[Message] = []
    sender_node.on_message = originated.append

    run(sender_node._do_broadcast(Message(content="secure ping", sender=NODE_A)))

    assert len(originated) == 1
    assert originated[0].content == "secure ping"
    assert not is_encrypted_content(originated[0].content)


def test_encrypted_signed_message_decrypts_before_ui_callback():
    alice_priv, alice_pub = _keypair()
    bob_priv, bob_pub = _keypair()

    configure_private_key(alice_priv)
    register_public_key(NODE_A, alice_pub)
    register_public_key(NODE_B, bob_pub)

    registry = InMemoryRegistry()
    registry.add_peer("127.0.0.1", 5001, alice_pub.decode())
    registry.add_peer("127.0.0.1", 5002, bob_pub.decode())

    wire = Message(content="secure ping", sender=NODE_A, ttl=0)
    sender_node = BroadcastNode("127.0.0.1", 5001, registry, enforce_signatures=True)
    sender_node.own_public_key_pem = alice_pub
    assert sender_node._encrypt_outgoing(wire) is True
    assert sender_node._sign_outgoing(wire) is True
    assert is_encrypted_content(wire.content)
    assert wire.signature

    configure_private_key(bob_priv)
    receiver_node = BroadcastNode("127.0.0.1", 5002, registry, enforce_signatures=True)
    delivered: list[Message] = []
    receiver_node.on_message = delivered.append

    responses = run(_deliver_one(receiver_node, wire))
    assert responses == [{"ack": wire.id}]
    assert len(delivered) == 1
    assert delivered[0].content == "secure ping"
    assert not is_encrypted_content(delivered[0].content)

    display = Service(refreshes={"messages": lambda _msgs: None})
    display._messages.clear()
    display.history_service = None
    display.message_received(delivered[0])

    assert display.get_messages()[-1]["content"] == "secure ping"

import asyncio
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from distribution import BroadcastNode, InMemoryRegistry, Message
from security import clear_keys, configure_private_key, sign


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


@pytest.fixture
def keypair():
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


async def _handle_one(node, message):
    ws = FakeWebSocket([message.to_json()])
    await node._handle_ws(ws)
    return ws.sent


def run(coro):
    return asyncio.run(coro)


def test_valid_signed_message_is_acked_and_delivered(keypair):
    private_pem, public_pem = keypair
    configure_private_key(private_pem)

    registry = InMemoryRegistry()
    registry.add_peer("127.0.0.1", 5002, public_pem)
    node = BroadcastNode("127.0.0.1", 5001, registry, enforce_signatures=True)
    delivered = []
    node.on_message = delivered.append

    msg = sign(Message(content="ok", sender="127.0.0.1:5002", ttl=0))
    responses = run(_handle_one(node, msg))

    assert responses == [{"ack": msg.id}]
    assert delivered == [msg]
    assert msg.id in node._seen


def test_unsigned_message_is_nacked_before_dedup_or_delivery(keypair):
    _private_pem, public_pem = keypair

    registry = InMemoryRegistry()
    registry.add_peer("127.0.0.1", 5002, public_pem)
    node = BroadcastNode("127.0.0.1", 5001, registry, enforce_signatures=True)
    delivered = []
    node.on_message = delivered.append

    msg = Message(content="unsigned", sender="127.0.0.1:5002", id="unsigned-1", ttl=0)
    responses = run(_handle_one(node, msg))

    assert responses == [{"nack": msg.id, "reason": "signature_failed_or_missing_key"}]
    assert delivered == []
    assert msg.id not in node._seen


def test_signed_message_without_sender_key_is_nacked_before_dedup(keypair):
    private_pem, _public_pem = keypair
    configure_private_key(private_pem)

    node = BroadcastNode("127.0.0.1", 5001, InMemoryRegistry(), enforce_signatures=True)
    msg = sign(Message(content="missing key", sender="127.0.0.1:5002", id="missing-key", ttl=0))
    responses = run(_handle_one(node, msg))

    assert responses == [{"nack": msg.id, "reason": "signature_failed_or_missing_key"}]
    assert msg.id not in node._seen


def test_bad_signature_is_nacked_before_dedup_or_forward(keypair):
    private_pem, public_pem = keypair
    configure_private_key(private_pem)

    registry = InMemoryRegistry()
    registry.add_peer("127.0.0.1", 5002, public_pem)
    node = BroadcastNode("127.0.0.1", 5001, registry, enforce_signatures=True)
    forwarded = []

    async def fake_forward(message):
        forwarded.append(message)

    node._forward = fake_forward

    msg = sign(Message(content="original", sender="127.0.0.1:5002", id="tampered", ttl=3))
    msg.content = "tampered"
    responses = run(_handle_one(node, msg))

    assert responses == [{"nack": msg.id, "reason": "signature_failed_or_missing_key"}]
    assert forwarded == []
    assert msg.id not in node._seen


def test_direct_send_signs_recovery_message_when_security_is_enabled(keypair):
    private_pem, _public_pem = keypair
    configure_private_key(private_pem)

    node = BroadcastNode(
        "127.0.0.1",
        5001,
        InMemoryRegistry(),
        enforce_signatures=True,
    )
    sent = []

    async def fake_send(host, port, message):
        wire = node._prepare_outgoing_for_peer(message, host, port)
        sent.append((host, port, wire))

    node._send_with_retry = fake_send

    msg = Message(content="history chunk", sender=node.address, id="history-signed", ttl=5)
    run(node._send_to_peer("127.0.0.1", 5002, msg))

    assert len(sent) == 1
    assert sent[0][0:2] == ("127.0.0.1", 5002)
    assert sent[0][2].ttl == 0
    assert sent[0][2].signature

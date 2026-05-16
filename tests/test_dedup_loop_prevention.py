import asyncio

import pytest
import distribution.broadcast_node as broadcast_module
from distribution import BroadcastNode, InMemoryRegistry, Message


def run(coro):
    return asyncio.run(coro)


def make_node():
    registry = InMemoryRegistry()
    registry.add_peer("127.0.0.1", 5001)
    registry.add_peer("127.0.0.1", 5002)
    registry.add_peer("127.0.0.1", 5003)
    return BroadcastNode("127.0.0.1", 5001, registry)


def test_deduplicate_returns_true_once_false_after_that():
    node = make_node()

    assert node.deduplicate("msg-1") is True
    assert node.deduplicate("msg-1") is False
    assert node.deduplicate("msg-2") is True


def test_receive_processes_duplicate_only_once():
    node = make_node()
    delivered = []
    forwarded = []
    node.on_message = delivered.append

    async def fake_forward(message):
        forwarded.append(message)

    node._forward = fake_forward
    msg = Message(content="hello", sender="127.0.0.1:5002", id="same-id", ttl=3)

    assert run(node._receive(msg)) is True
    assert run(node._receive(msg)) is False

    assert len(delivered) == 1
    assert len(forwarded) == 1
    assert forwarded[0].ttl == 2


def test_receive_ttl_zero_delivers_but_does_not_forward():
    node = make_node()
    delivered = []
    forwarded = []
    node.on_message = delivered.append

    async def fake_forward(message):
        forwarded.append(message)

    node._forward = fake_forward
    msg = Message(content="stop here", sender="127.0.0.1:5002", id="ttl-zero", ttl=0)

    assert run(node._receive(msg)) is True

    assert delivered == [msg]
    assert forwarded == []


def test_receive_does_not_mutate_delivered_message_ttl():
    node = make_node()
    delivered = []
    forwarded = []
    node.on_message = delivered.append

    async def fake_forward(message):
        forwarded.append(message)

    node._forward = fake_forward
    msg = Message(content="keep original ttl", sender="127.0.0.1:5002", id="ttl-copy", ttl=4)

    assert run(node._receive(msg)) is True

    assert delivered[0].ttl == 4
    assert forwarded[0].ttl == 3


def test_local_duplicate_broadcast_is_not_delivered_or_forwarded_twice():
    node = make_node()
    delivered = []
    forwarded = []
    node.on_message = delivered.append

    async def fake_forward(message):
        forwarded.append(message)

    node._forward = fake_forward
    msg = Message(content="local duplicate", sender=node.address, id="local-id", ttl=3)

    assert run(node._do_broadcast(msg)) is True
    assert run(node._do_broadcast(msg)) is False

    assert len(delivered) == 1
    assert len(forwarded) == 1


def test_start_fails_fast_when_websockets_is_missing(monkeypatch):
    node = make_node()
    monkeypatch.setattr(broadcast_module, "websockets", None)

    with pytest.raises(RuntimeError, match="websockets is required"):
        node.start()


def test_direct_send_targets_one_peer_and_forces_ttl_zero():
    node = make_node()
    sent = []

    async def fake_send(host, port, message):
        sent.append((host, port, message))

    node._send_with_retry = fake_send
    msg = Message(content="history chunk", sender=node.address, id="history-1", ttl=5)

    run(node._send_to_peer("127.0.0.1", 5003, msg))

    assert len(sent) == 1
    assert sent[0][0:2] == ("127.0.0.1", 5003)
    assert sent[0][2].ttl == 0
    assert msg.ttl == 5


def test_direct_send_does_not_use_broadcast_forwarding():
    node = make_node()
    forwarded = []
    sent = []

    async def fake_forward(message):
        forwarded.append(message)

    async def fake_send(host, port, message):
        sent.append((host, port, message))

    node._forward = fake_forward
    node._send_with_retry = fake_send
    msg = Message(content="private replay", sender=node.address, id="history-2", ttl=10)

    run(node._send_to_peer("127.0.0.1", 5002, msg))

    assert forwarded == []
    assert len(sent) == 1

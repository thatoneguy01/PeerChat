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

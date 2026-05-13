"""
End-to-end integration test.

Wires BroadcastNode together with stubs for Security, Peer Discovery (via
InMemoryRegistry — same role), and History, and asserts that a signed message
flows through all layers and arrives on every peer exactly once.

When the real modules ship, swap the stubs for them and re-run.
"""

import time

import pytest

from distribution import BroadcastNode, InMemoryRegistry, Message

from .stubs import fake_security
from .stubs.fake_storage import FakeStorage
from .stubs.listeners import Listeners


BASE_PORT = 6001
NODE_COUNT = 3
SETTLE = 0.6


def _make_mesh(count: int, base_port: int):
    """Build `count` BroadcastNodes sharing one InMemoryRegistry (peer discovery stub)."""
    registry = InMemoryRegistry()
    for i in range(count):
        registry.add_peer("127.0.0.1", base_port + i)

    nodes, storages = [], []
    for i in range(count):
        node = BroadcastNode("127.0.0.1", base_port + i, registry)
        storage = FakeStorage()
        listeners = Listeners()

        def guarded(msg, s=storage):
            if fake_security.verify(msg):
                s.append(msg)

        listeners.register(guarded)
        node.on_message = listeners.dispatch
        node.start()
        nodes.append(node)
        storages.append(storage)

    time.sleep(SETTLE)      # let WS servers bind
    return nodes, storages


def _teardown(nodes):
    for node in nodes:
        node.stop()


def test_signed_message_reaches_every_peer_once():
    """Happy path: sign → broadcast → every peer delivers once, verify passes."""
    nodes, storages = _make_mesh(NODE_COUNT, BASE_PORT)
    try:
        msg = fake_security.sign(
            Message(content="hello world", sender=nodes[0].address)
        )
        nodes[0].broadcast(msg)
        time.sleep(SETTLE + 1.0)

        for s in storages:
            assert len(s) == 1, f"expected 1 delivery, got {len(s)}"
            assert s.messages[0].content == "hello world"
    finally:
        _teardown(nodes)


def test_unsigned_message_is_dropped_by_storage():
    """Security failure: a message with a bad signature is filtered by the stub."""
    nodes, storages = _make_mesh(NODE_COUNT, BASE_PORT + 10)
    try:
        msg = Message(content="tampered", sender=nodes[0].address)
        msg.signature = "not-a-real-sig"
        nodes[0].broadcast(msg)
        time.sleep(SETTLE + 1.0)

        for s in storages:
            assert len(s) == 0, "tampered message should have been dropped by verify()"
    finally:
        _teardown(nodes)


def test_duplicate_broadcast_still_delivers_once():
    """Dedup works end-to-end: broadcasting the same msg twice delivers once per peer."""
    nodes, storages = _make_mesh(NODE_COUNT, BASE_PORT + 20)
    try:
        msg = fake_security.sign(
            Message(content="only-once", sender=nodes[0].address)
        )
        nodes[0].broadcast(msg)
        nodes[0].broadcast(msg)     # same Message, same id
        time.sleep(SETTLE + 1.0)

        for s in storages:
            assert len(s) == 1
    finally:
        _teardown(nodes)


def test_multiple_listeners_all_see_every_message():
    """History + UI co-exist via Listeners shim."""
    registry = InMemoryRegistry()
    port = BASE_PORT + 30
    registry.add_peer("127.0.0.1", port)

    node = BroadcastNode("127.0.0.1", port, registry)
    storage = FakeStorage()
    ui_log = []

    listeners = Listeners()
    listeners.register(storage.append)
    listeners.register(lambda m: ui_log.append(m.content))
    node.on_message = listeners.dispatch
    node.start()
    time.sleep(SETTLE)

    try:
        msg = fake_security.sign(Message(content="fan-out", sender=node.address))
        node.broadcast(msg)
        time.sleep(SETTLE + 0.5)

        assert len(storage) == 1
        assert ui_log == ["fan-out"]
    finally:
        node.stop()


def test_direct_send_reaches_only_target_peer():
    """History replay can send one chunk to one peer without broadcast fanout."""
    registry = InMemoryRegistry()
    base_port = BASE_PORT + 40
    for i in range(NODE_COUNT):
        registry.add_peer("127.0.0.1", base_port + i)

    nodes, inboxes = [], []
    for i in range(NODE_COUNT):
        node = BroadcastNode("127.0.0.1", base_port + i, registry)
        inbox = []
        node.on_message = inbox.append
        node.start()
        nodes.append(node)
        inboxes.append(inbox)

    time.sleep(SETTLE)

    try:
        msg = Message(content="history chunk", sender=nodes[0].address, ttl=10)
        nodes[0].send_to_peer("127.0.0.1", base_port + 1, msg)
        time.sleep(SETTLE + 0.5)

        assert inboxes[0] == []
        assert len(inboxes[1]) == 1
        assert inboxes[1][0].content == "history chunk"
        assert inboxes[1][0].ttl == 0
        assert inboxes[2] == []
        assert msg.ttl == 10
    finally:
        _teardown(nodes)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

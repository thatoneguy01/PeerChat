import socket
import time

import distribution.broadcast_node as broadcast_module
from distribution import BroadcastNode, InMemoryRegistry, Message


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def node_at(port, registry=None, **kwargs):
    registry = registry or InMemoryRegistry()
    registry.add_peer("127.0.0.1", port)
    return BroadcastNode("127.0.0.1", port, registry, **kwargs)


def test_stop_releases_port_so_node_can_restart_on_same_port():
    port = free_port()

    first = node_at(port)
    first.start()
    first.stop()

    second = node_at(port)
    try:
        second.start()
        assert second.check_peer_ready("127.0.0.1", port)
    finally:
        second.stop()


def test_ready_probe_returns_true_only_when_peer_answers():
    port_a = free_port()
    port_b = free_port()
    registry = InMemoryRegistry()
    registry.add_peer("127.0.0.1", port_a)
    registry.add_peer("127.0.0.1", port_b)

    node_a = BroadcastNode("127.0.0.1", port_a, registry)
    node_b = BroadcastNode("127.0.0.1", port_b, registry)

    try:
        node_a.start()
        assert node_a.check_peer_ready("127.0.0.1", port_b, timeout=0.2) is False

        node_b.start()
        assert node_a.check_peer_ready("127.0.0.1", port_b) is True
    finally:
        node_a.stop()
        node_b.stop()


def test_failed_direct_send_is_queued_and_flushed_after_peer_returns(monkeypatch):
    monkeypatch.setattr(broadcast_module, "RETRY_BACKOFF", 0.01)

    port_a = free_port()
    port_b = free_port()
    registry = InMemoryRegistry()
    registry.add_peer("127.0.0.1", port_a)
    registry.add_peer("127.0.0.1", port_b)

    node_a = BroadcastNode("127.0.0.1", port_a, registry)
    node_b = BroadcastNode("127.0.0.1", port_b, registry)
    inbox = []
    node_b.on_message = inbox.append

    try:
        node_a.start()
        msg = Message(content="recover me later", sender=node_a.address, ttl=10)
        node_a.send_to_peer("127.0.0.1", port_b, msg)

        deadline = time.time() + 3.0
        while time.time() < deadline and node_a.pending_count("127.0.0.1", port_b) == 0:
            time.sleep(0.05)
        assert node_a.pending_count("127.0.0.1", port_b) == 1

        node_b.start()
        flushed = node_a.retry_pending("127.0.0.1", port_b).result(timeout=5.0)

        assert flushed == 1
        assert node_a.pending_count("127.0.0.1", port_b) == 0
        assert len(inbox) == 1
        assert inbox[0].content == "recover me later"
        assert inbox[0].ttl == 0
        assert msg.ttl == 10
    finally:
        node_a.stop()
        node_b.stop()


def test_pending_queue_is_bounded():
    port = free_port()
    node = node_at(port, pending_queue_limit=2)

    node._queue_pending("127.0.0.1", 5001, Message(content="old", sender=node.address, id="m1"))
    node._queue_pending("127.0.0.1", 5001, Message(content="mid", sender=node.address, id="m2"))
    node._queue_pending("127.0.0.1", 5001, Message(content="new", sender=node.address, id="m3"))

    queued = node._take_pending("127.0.0.1", 5001)
    assert [msg.id for msg in queued] == ["m2", "m3"]

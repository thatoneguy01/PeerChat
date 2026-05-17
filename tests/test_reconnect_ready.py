import asyncio
import json
import socket
import time

import websockets

import distribution.broadcast_node as broadcast_module
from distribution import BroadcastNode, InMemoryRegistry, Message


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until(predicate, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def queued_count(node, host, port):
    with node._retry_lock:
        return len(node._retry_queue.get(f"{host}:{port}", []))


async def hello_probe(host, port):
    async with websockets.connect(f"ws://{host}:{port}", open_timeout=1, close_timeout=1) as ws:
        await ws.send(json.dumps({"type": "hello", "sender": "test-probe"}))
        raw = await asyncio.wait_for(ws.recv(), timeout=1)
        return json.loads(raw)


def wait_for_hello(host, port, timeout=3.0):
    ack = {}

    def probe():
        nonlocal ack
        try:
            ack = asyncio.run(hello_probe(host, port))
            return True
        except OSError:
            return False

    assert wait_until(probe, timeout=timeout)
    return ack


def test_stop_releases_port_so_node_can_restart_on_same_port():
    port = free_port()
    registry = InMemoryRegistry()
    registry.add_peer("127.0.0.1", port)

    first = BroadcastNode("127.0.0.1", port, registry)
    first.start()
    wait_for_hello("127.0.0.1", port)
    first.stop()

    second = BroadcastNode("127.0.0.1", port, registry)
    try:
        second.start()
        ack = wait_for_hello("127.0.0.1", port)
        assert ack["type"] == "hello_ack"
        assert ack["sender"] == second.address
    finally:
        second.stop()


def test_hello_probe_confirms_two_way_websocket_path():
    port = free_port()
    registry = InMemoryRegistry()
    registry.add_peer("127.0.0.1", port)

    node = BroadcastNode("127.0.0.1", port, registry)
    try:
        node.start()
        ack = wait_for_hello("127.0.0.1", port)
        assert ack == {"type": "hello_ack", "sender": node.address}
    finally:
        node.stop()


def test_failed_direct_send_is_queued_and_flushed_after_peer_returns(monkeypatch):
    monkeypatch.setattr(broadcast_module, "ACK_TIMEOUT", 0.05)
    monkeypatch.setattr(broadcast_module, "RETRY_BACKOFF", 0.01)
    monkeypatch.setattr(broadcast_module, "MAX_RETRIES", 2)

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

        assert wait_until(lambda: queued_count(node_a, "127.0.0.1", port_b) == 1)

        node_b.start()
        future = asyncio.run_coroutine_threadsafe(
            node_a._do_handshake("127.0.0.1", port_b), node_a._loop
        )
        future.result(timeout=2)

        assert wait_until(lambda: len(inbox) == 1 and queued_count(node_a, "127.0.0.1", port_b) == 0)
        assert inbox[0].content == "recover me later"
        assert inbox[0].ttl == 0
        assert msg.ttl == 10
    finally:
        node_a.stop()
        node_b.stop()

"""Integration: encrypt -> sign -> broadcast -> verify -> decrypt across a mesh."""

import time

import pytest

from distribution import BroadcastNode, InMemoryRegistry, Message
from security import SecureChatSession

pytestmark = pytest.mark.integration


BASE_PORT = 6101
NODE_COUNT = 3
SETTLE = 0.8


def _build_secure_mesh(count: int, base_port: int):
    registry = InMemoryRegistry()
    for i in range(count):
        registry.add_peer("127.0.0.1", base_port + i)

    sessions: dict[str, SecureChatSession] = {}
    for i in range(count):
        addr = f"127.0.0.1:{base_port + i}"
        sessions[addr] = SecureChatSession(user_id=addr)

    for addr, session in sessions.items():
        for other_addr, other in sessions.items():
            if other_addr != addr:
                session.register_peer(other_addr, other.public_key_pem)

    nodes = []
    received: dict[str, list[str]] = {a: [] for a in sessions}

    for i in range(count):
        addr = f"127.0.0.1:{base_port + i}"
        node = BroadcastNode("127.0.0.1", base_port + i, registry)
        session = sessions[addr]

        def handler(msg, s=session, a=addr, r=received):
            text = s.open_incoming(msg)
            if text is not None:
                r[a].append(text)

        node.on_message = handler
        node.start()
        nodes.append(node)

    time.sleep(SETTLE)
    return nodes, sessions, received


def test_encrypted_signed_message_reaches_all_peers():
    nodes, sessions, received = _build_secure_mesh(NODE_COUNT, BASE_PORT)
    try:
        origin_addr = f"127.0.0.1:{BASE_PORT}"
        msg = sessions[origin_addr].prepare_outgoing(
            plaintext="integration hello",
            sender_address=origin_addr,
        )
        nodes[0].broadcast(msg)
        time.sleep(SETTLE + 1.0)

        for addr, texts in received.items():
            assert texts == ["integration hello"], f"{addr} got {texts}"
    finally:
        for node in nodes:
            node.stop()


def test_bad_signature_is_dropped():
    nodes, sessions, received = _build_secure_mesh(NODE_COUNT, BASE_PORT + 10)
    try:
        origin_addr = f"127.0.0.1:{BASE_PORT + 10}"
        msg = sessions[origin_addr].prepare_outgoing(
            plaintext="should not arrive",
            sender_address=origin_addr,
        )
        msg.signature = "invalid"
        nodes[0].broadcast(msg)
        time.sleep(SETTLE + 1.0)

        assert all(len(v) == 0 for v in received.values())
    finally:
        for node in nodes:
            node.stop()

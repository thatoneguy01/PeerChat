"""
Demo: 3 gossip nodes exchange a message.

Run with:
    python demo.py

Expected output (order may vary):
    [Node :5001] SENT: 'Hello from Node 1!'
    [Node :5002] received: 'Hello from Node 1!'
    [Node :5003] received: 'Hello from Node 1!'
"""

import time
import logging
from distribution import GossipNode, InMemoryRegistry, Message

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
)


def make_handler(label: str):
    def handler(msg: Message):
        print(f"[{label}] received: {msg.content!r}  (id={msg.id[:8]})")
    return handler


def main():
    # ── Shared peer list (normally provided by Discovery team) ────────────────
    registry = InMemoryRegistry()
    for port in (5001, 5002, 5003):
        registry.add_peer("127.0.0.1", port)

    # ── Start three nodes ─────────────────────────────────────────────────────
    nodes = []
    for port in (5001, 5002, 5003):
        node = GossipNode("127.0.0.1", port, registry, fanout=2)
        node.on_message = make_handler(f"Node :{port}")
        node.start()
        nodes.append(node)

    time.sleep(0.1)   # let servers finish binding

    # ── Node 1 sends a message ────────────────────────────────────────────────
    msg = Message(content="Hello from Node 1!", sender="127.0.0.1:5001")
    print(f"\n[Node :5001] SENT: {msg.content!r}\n")
    nodes[0].broadcast(msg)

    time.sleep(1)     # let gossip propagate

    print("\n[Demo complete — each node should have received the message once]")
    for node in nodes:
        node.stop()


if __name__ == "__main__":
    main()

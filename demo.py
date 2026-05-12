"""
Demo: 10 broadcast nodes exchange a message over WebSockets.

Run with:
    python3.11 demo.py

Expected output (received order may vary):
    [Node :5001] SENT: 'Hello from Node 1!'
    [Node :5001] received: 'Hello from Node 1!'  (id=xxxxxxxx)
    [Node :5002] received: 'Hello from Node 1!'  (id=xxxxxxxx)
    ...
    [Node :5010] received: 'Hello from Node 1!'  (id=xxxxxxxx)

    [Demo complete — 10/10 nodes received the message]
"""

import time
import logging
import threading
from distribution import BroadcastNode, InMemoryRegistry, Message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
)

PORTS = list(range(5001, 5011))   # 5001 through 5010


def main():
    # ── Shared peer list (provided by Discovery team in the real system) ──────
    registry = InMemoryRegistry()
    for port in PORTS:
        registry.add_peer("127.0.0.1", port)

    # ── Track how many nodes received the message ─────────────────────────────
    received_count = 0
    count_lock = threading.Lock()

    def make_handler(label: str):
        def handler(msg: Message):
            nonlocal received_count
            with count_lock:
                received_count += 1
            print(f"  [{label}] received: {msg.content!r}  (id={msg.id[:8]})")
        return handler

    # ── Start 10 nodes ────────────────────────────────────────────────────────
    print(f"Starting {len(PORTS)} nodes on ports {PORTS[0]}–{PORTS[-1]}...\n")
    nodes = []
    for port in PORTS:
        node = BroadcastNode("127.0.0.1", port, registry)
        node.on_message = make_handler(f"Node :{port}")
        node.start()
        nodes.append(node)

    time.sleep(0.5)   # wait for all WebSocket servers to finish binding

    # ── Node 1 broadcasts a message ───────────────────────────────────────────
    msg = Message(content="Hello from Node 1!", sender="127.0.0.1:5001")
    print(f"[Node :5001] SENT: {msg.content!r}\n")
    nodes[0].broadcast(msg)

    time.sleep(2.0)   # wait for broadcast + ACKs to complete across all 10 nodes

    # ── Results ───────────────────────────────────────────────────────────────
    print(f"\n[Demo complete — {received_count}/{len(PORTS)} nodes received the message]")

    for node in nodes:
        node.stop()


if __name__ == "__main__":
    main()

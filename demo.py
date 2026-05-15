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
import asyncio
from distribution import BroadcastNode, InMemoryRegistry, Message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
)

PORTS = list(range(5001, 5011))   # 5001 through 5010


def section(title: str) -> None:
    print(f"\n{'=' * 55}")
    print(f"  {title}")
    print(f"{'=' * 55}\n")


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

    demo_causal()


# ── Causal ordering demos (messages injected directly into BroadcastNode) ────────────────

def make_broadcast_node(port: int, label: str):
    node = BroadcastNode("127.0.0.1", port, InMemoryRegistry())
    log = []
    node.on_message = lambda msg: log.append(
        f"  [{label}] delivered: {msg.content!r}  vc={msg.vector_clock}"
    )
    return node, log


def demo_causal() -> None:
    section("Causal ordering: single sender, M2 arrives before M1")
    receiver, log = make_broadcast_node(19100, "receiver")
    sender_addr = "127.0.0.1:9001"
    m1 = Message(content="M1: hello", sender=sender_addr, vector_clock={sender_addr: 1})
    m2 = Message(content="M2: reply", sender=sender_addr, vector_clock={sender_addr: 2})
    print("  Injecting M2 first (M1 not yet seen)...")
    asyncio.run(receiver._receive(m2))
    print(f"  Delivered so far: {len(log)}  (expected 0, held back)\n")
    print("  Injecting M1...")
    asyncio.run(receiver._receive(m1))
    print(f"  Delivered so far: {len(log)}  (expected 2, cascade flushed)\n")
    for line in log:
        print(line)

    section("Causal ordering: B replies to A, C sees B's reply first")
    node_c, log_c = make_broadcast_node(19101, "C")
    addr_a, addr_b = "127.0.0.1:9010", "127.0.0.1:9011"
    ma1 = Message(content="A: hello", sender=addr_a, vector_clock={addr_a: 1})
    mb1 = Message(content="B: hi A!", sender=addr_b, vector_clock={addr_a: 1, addr_b: 1})
    print("  C receives B's reply before A's original...")
    asyncio.run(node_c._receive(mb1))
    print(f"  Delivered so far: {len(log_c)}  (expected 0, waiting for A)\n")
    print("  C now receives A's original message...")
    asyncio.run(node_c._receive(ma1))
    print(f"  Delivered so far: {len(log_c)}  (expected 2, B's reply flushed)\n")
    for line in log_c:
        print(line)

    section("Causal ordering: three messages, all arrive in reverse order")
    node_d, log_d = make_broadcast_node(19102, "D")
    addr_s = "127.0.0.1:9020"
    msgs = [
        Message(content=f"msg {i}", sender=addr_s, vector_clock={addr_s: i})
        for i in range(1, 4)
    ]
    print("  Injecting msg 3, msg 2, msg 1 in that order...")
    for msg in reversed(msgs):
        asyncio.run(node_d._receive(msg))
        print(f"  After {msg.content!r} arrives: {len(log_d)} delivered")
    print("\n  Final delivery order:")
    for line in log_d:
        print(line)

    section("Causal ordering: broadcast() stamps vector clock on outgoing messages")
    sender_node, _ = make_broadcast_node(19103, "sender")
    for i in range(3):
        msg = Message(content=f"message {i + 1}", sender=sender_node.address)
        asyncio.run(sender_node._do_broadcast(msg))
        print(f"  sent: {msg.content!r}  vc={msg.vector_clock}")


def demo_sync_vc() -> None:
    section("sync_vector_clock: node restarts with zeroed VC, live messages arrive referencing history")

    node, log = make_broadcast_node(19104, "node3")
    node1 = "192.168.0.109:5001"
    node2 = "192.168.0.110:5002"

    # node3 just restarted. History recovery saved 2 messages from node1
    # and 1 from node2 into the store, but BroadcastNode._vc is still zeroed.
    # Two live messages now arrive that reference that history.
    live1 = Message(content="live: node1 msg 3 (refs history)", sender=node1,
                    vector_clock={node1: 3, node2: 1})
    live2 = Message(content="live: node2 msg 2 (refs history)", sender=node2,
                    vector_clock={node1: 3, node2: 2})

    print("  Two live messages arrive; VC is zeroed so both are held back.")
    asyncio.run(node._receive(live1))
    asyncio.run(node._receive(live2))
    print(f"  Delivered so far: {len(log)}  (expected 0 — VC is zeroed, hold-back stuck)\n")

    # History team calls sync_vector_clock after recovery completes.
    # Recovered VC reflects what is now in the store: node1 sent 2, node2 sent 1.
    recovered_vc = {node1: 2, node2: 1}
    print(f"  Calling sync_vector_clock({recovered_vc}) to seed VC from recovered history ...")
    asyncio.run(node._apply_vc_sync(recovered_vc))
    print(f"  Delivered so far: {len(log)}  (expected 2 — both live messages unblocked)\n")
    for line in log:
        print(line)


if __name__ == "__main__":
    main()
    demo_sync_vc()

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
import os
import argparse
import json
from distribution import BroadcastNode, InMemoryRegistry, Message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
)

PORTS = list(range(5001, 5011))   # 5001 through 5010
ENABLE_HISTORY = os.getenv("PEERCHAT_HISTORY") == "1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PeerChat demo and interactive runner.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, help="Run one interactive node on this port.")
    parser.add_argument(
        "--peers",
        nargs="*",
        type=int,
        default=[],
        help="Peer ports for interactive mode, for example: --peers 5002 5003",
    )
    return parser.parse_args()


def section(title: str) -> None:
    print(f"\n{'=' * 55}")
    print(f"  {title}")
    print(f"{'=' * 55}\n")


def main():
    args = parse_args()
    if args.port is not None:
        run_interactive(args)
        return

    run_demo()


def run_interactive(args: argparse.Namespace) -> None:
    logging.getLogger().setLevel(logging.WARNING)

    registry = InMemoryRegistry()
    registry.add_peer(args.host, args.port)
    for peer_port in args.peers:
        registry.add_peer(args.host, peer_port)

    node = BroadcastNode(args.host, args.port, registry)
    history = []
    wiring = None

    def handle_message(msg: Message):
        if wiring:
            sync_node_clock_from_store(node, wiring.store)
            if is_recovery_transport(msg):
                return

        history.append(msg)
        print(f"\n[{node.address}] from {msg.sender}: {msg.content}", flush=True)

    if ENABLE_HISTORY:
        from storage import wire_node

        wiring = wire_node(
            node=node,
            host=args.host,
            port=args.port,
            pull_recovery_on_start=True,
        )
        wiring.listeners.register(handle_message)
    else:
        node.on_message = handle_message
        node.start()

    peer_list = ", ".join(str(port) for port in args.peers) or "none"
    print(f"Running {node.address} | peers: {peer_list}")
    print("Commands: send <message> | show | quit")

    try:
        while True:
            text = input("> ").strip()
            if not text:
                continue

            if text == "quit":
                break

            if text == "show":
                messages = wiring.store.get_recent(100) if wiring else history
                print("History:")
                for index, msg in enumerate(messages, start=1):
                    print(f"{index}. [{msg.sender}] {msg.content}")
                continue

            if text.startswith("send "):
                text = text[5:].strip()

            if text:
                if wiring:
                    sync_node_clock_from_store(node, wiring.store)
                node.broadcast(Message(content=text, sender=node.address))
    except KeyboardInterrupt:
        print()
    finally:
        node.stop()
        time.sleep(0.2)


def is_recovery_transport(msg: Message) -> bool:
    try:
        payload = json.loads(msg.content)
    except (TypeError, json.JSONDecodeError):
        return False

    return payload.get("type") in {"recover_request", "history_chunk"}


def sync_node_clock_from_store(node: BroadcastNode, store) -> None:
    latest = store.get_latest_vector_clock()
    if latest:
        node._vc.merge(latest)


def run_demo():
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
        if ENABLE_HISTORY:
            from storage import wire_node

            wiring = wire_node(
                node=node,
                host="127.0.0.1",
                port=port,
                pull_recovery_on_start=True,
            )
            wiring.listeners.register(make_handler(f"Node :{port}"))
        else:
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


if __name__ == "__main__":
    main()

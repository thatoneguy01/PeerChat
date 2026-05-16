"""
Interactive history/recovery demo.

Run one terminal per node:
    python demo.py --port 5001 --peers 5002 5003 --clean
    python demo.py --port 5002 --peers 5001 5003 --clean
    python demo.py --port 5003 --peers 5001 5002 --clean

Commands:
    send <text>   broadcast a chat message
    show          print local stored history
    vc            print this node's latest vector clock
    list snapshots
    quit          stop this node
"""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from distribution import BroadcastNode, InMemoryRegistry, Message


ROOT = Path(__file__).resolve().parent
HISTORY_ROOT = ROOT / "message-history"
if str(HISTORY_ROOT) not in sys.path:
    sys.path.insert(0, str(HISTORY_ROOT))

from storage import wire_node  # noqa: E402
from storage import local_message_store as store_paths  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one history-enabled PeerChat node.")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--peers", nargs="*", type=int, default=[])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--clean", action="store_true")
    return parser.parse_args()


def is_recovery_message(msg: Message) -> bool:
    try:
        payload = json.loads(msg.content)
    except (TypeError, json.JSONDecodeError):
        return False
    return payload.get("type") in {"recover_request", "history_chunk"}


def use_storage_for_port(port: int, clean: bool) -> None:
    root = HISTORY_ROOT / "runtime" / str(port)
    if clean:
        shutil.rmtree(root, ignore_errors=True)

    store_paths.LOG_DIR = root / "logs"
    store_paths.INDEX_DIR = root / "index"
    store_paths.SNAPSHOT_DIR = root / "snapshots"
    store_paths.ACTIVE_LOG = store_paths.LOG_DIR / "active.log.jsonl"
    store_paths.MSG_ID_INDEX = store_paths.INDEX_DIR / "message_id.index"
    store_paths.SENDER_INDEX = store_paths.INDEX_DIR / "sender_seq.index"
    store_paths.VC_INDEX = store_paths.INDEX_DIR / "latest_vector_clock.json"
    store_paths.RECOVERY_STATE = store_paths.INDEX_DIR / "recovery_state.json"


def build_registry(host: str, port: int, peer_ports: list[int]) -> InMemoryRegistry:
    registry = InMemoryRegistry()
    registry.add_peer(host, port)
    for peer_port in peer_ports:
        registry.add_peer(host, peer_port)
    return registry


def main() -> None:
    args = parse_args()
    use_storage_for_port(args.port, args.clean)

    registry = build_registry(args.host, args.port, args.peers)
    node = BroadcastNode(args.host, args.port, registry)
    wiring = wire_node(
        node=node,
        host=args.host,
        port=args.port,
        pull_recovery_on_start=True,
    )

    if hasattr(node, "sync_vector_clock"):
        node.sync_vector_clock(wiring.store.get_latest_vector_clock())

    def print_chat(msg: Message) -> None:
        if is_recovery_message(msg):
            return
        print(f"\n[{msg.sender}] {msg.content}", flush=True)

    wiring.listeners.register(print_chat)

    print(f"Node {args.host}:{args.port} running. Peers: {args.peers}")
    print("Commands: send <text> | show | vc | list snapshots | quit")

    try:
        while True:
            command = input("> ").strip()
            if not command:
                continue

            if command == "quit":
                break

            if command == "show":
                for msg in wiring.store.get_recent(100):
                    print(f"[{msg.sender}] {msg.content} vc={msg.vector_clock}")
                continue

            if command == "vc":
                print(wiring.store.get_latest_vector_clock())
                continue

            if command == "list snapshots":
                for meta in wiring.store.list_snapshots():
                    print(meta)
                continue

            text = command[5:].strip() if command.startswith("send ") else command
            if text:
                if hasattr(node, "sync_vector_clock"):
                    node.sync_vector_clock(wiring.store.get_latest_vector_clock())
                node.broadcast(Message(content=text, sender=node.address))
    finally:
        node.stop()
        time.sleep(0.2)


if __name__ == "__main__":
    main()

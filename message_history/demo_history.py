import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from distribution import BroadcastNode, InMemoryRegistry, Message
from message_history.storage import HistoryService
from peer_discovery.network.net_utils import get_lan_ip


def parse_peer(value: str) -> tuple[str, int]:
    try:
        host, port_text = value.rsplit(":", 1)
        return host, int(port_text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("peer must look like host:port") from exc


def format_message(msg) -> str:
    timestamp = time.strftime("%H:%M:%S", time.localtime(msg.timestamp))
    seq = msg.vector_clock.get(msg.sender, 0)
    return f"[{timestamp}] {msg.sender}#{seq}: {msg.content}"


def print_recent_messages(wiring, limit: int) -> None:
    messages = wiring.store.get_recent(limit)
    if not messages:
        print("(no messages)")
        return

    for msg in messages:
        print(format_message(msg))


def print_json(data) -> None:
    print(json.dumps(data, indent=2, sort_keys=True))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="PeerChat terminal demo with message history commands."
    )
    parser.add_argument(
        "--host",
        default=get_lan_ip(),
        help="LAN IP advertised to peers. Default: detected LAN IP.",
    )
    parser.add_argument(
        "--bind-host",
        default="0.0.0.0",
        help="Interface to bind the local WebSocket server.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5678,
        help="Local chat port.",
    )
    parser.add_argument(
        "--peer",
        nargs="*",
        type=parse_peer,
        default=[],
        help="Peer chat addresses, e.g. 192.168.0.117:5678 192.168.0.119:5678.",
    )
    parser.add_argument(
        "--storage-root",
        type=Path,
        default=None,
        help="Storage directory. Default: message_history/runtime/<port>.",
    )
    parser.add_argument(
        "--show-limit",
        type=int,
        default=50,
        help="Number of messages printed by the show command.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()

    registry = InMemoryRegistry()
    for host, port in args.peer:
        registry.add_peer(host, port)

    node = BroadcastNode(
        host=args.bind_host,
        port=args.port,
        peer_registry=registry,
    )
    node.address = f"{args.host}:{args.port}"
    history = HistoryService(
        node=node,
        host=args.host,
        port=args.port,
        storage_root=args.storage_root,
    )
    wiring = history.start()

    def on_message(msg: Message) -> None:
        result = history.handle_message(msg)
        if result.get("handled"):
            return
        print(f"\n{format_message(msg)}")
        if msg.sender != node.address:
            print("> ", end="", flush=True)

    node.on_message = on_message
    node.start()
    time.sleep(0.5)

    print(f"Node: {node.address}")
    print("Commands: show | vc | list snapshots | quit")
    print("Type any other text to send a chat message.")

    try:
        while True:
            command = input("> ").strip()
            if not command:
                continue

            if command == "quit":
                break

            if command == "show":
                print_recent_messages(wiring, args.show_limit)
                continue

            if command == "vc":
                print_json(wiring.store.get_latest_vector_clock())
                continue

            if command == "list snapshots":
                snapshots = wiring.store.list_snapshots()
                if not snapshots:
                    print("(no snapshots)")
                    continue
                for meta in snapshots:
                    print_json(meta)
                continue

            text = command
            if text:
                node.broadcast(Message(content=text, sender=node.address))
                time.sleep(0.05)
    finally:
        node.stop()


if __name__ == "__main__":
    main()

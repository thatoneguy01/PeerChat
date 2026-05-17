from distribution.broadcast_node import BroadcastNode
from distribution.message import Message
from distribution.peer_registry import InMemoryRegistry
import socket, time


def main():
    peer_registry = InMemoryRegistry()
    node = BroadcastNode(host="0.0.0.0", port=5000, peer_registry=peer_registry)
    # node = BroadcastNode(host="0.0.0.0", port=5000, peer_registry=peer_registry)
    node.start()
    time.sleep(11)  # wait for server to start


if __name__ == "__main__":
    main()
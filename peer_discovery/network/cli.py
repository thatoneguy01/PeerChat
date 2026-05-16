"""Command-line interface for the DiscoveryNode."""
import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from peer_discovery.network.config import DiscoveryConfig
from peer_discovery.network.discovery_node import DiscoveryNode


def main():
    parser = argparse.ArgumentParser(description="PeerChat Discovery Node")
    parser.add_argument("--port", type=int, required=True, help="Port to listen on")
    parser.add_argument("--advertise", type=str, required=True, help="Advertise address (host:port)")
    parser.add_argument("--room", type=str, required=True, help="Room ID to join")
    parser.add_argument("--bootstrap", type=str, action="append", default=[], help="Bootstrap peers (can be specified multiple times)")
    parser.add_argument("--name", type=str, default="Node", help="Display name for this node")
    parser.add_argument("--storage", type=str, default="~/.peerchat/storage", help="Storage directory for snapshots")
    parser.add_argument("--no-crypto", action="store_true", help="Disable cryptography (for testing only)")
    parser.add_argument("--debug", action="store_true", help="Enable DEBUG logging")
    
    args = parser.parse_args()
    
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    )
    
    storage_dir = Path(args.storage).expanduser()
    storage_dir.mkdir(parents=True, exist_ok=True)
    
    config = DiscoveryConfig(
        advertise_address=args.advertise,
        listen_port=args.port,
        bootstrap_peers=args.bootstrap,
        enable_crypto=not args.no_crypto
    )
    
    node = DiscoveryNode(
        room_id=args.room,
        config=config,
        storage_dir=str(storage_dir)
    )
    
    def signal_handler(sig, frame):
        logging.info("Shutting down...")
        node.stop()
        sys.exit(0)
        
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    node.start(display_name=args.name)
    
    # Keep main thread alive
    while True:
        try:
            time.sleep(1.0)
        except KeyboardInterrupt:
            signal_handler(signal.SIGINT, None)


if __name__ == "__main__":
    main()

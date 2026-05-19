"""Standalone CLI — DEPRECATED.

After the transport consolidation, DiscoveryNode requires a Distribution
BroadcastNode to function (all discovery traffic rides ws://host:5678).
Running discovery standalone via this CLI is no longer supported. Use
``python main.py`` instead, which wires up BroadcastNode, Security keystore,
and the Flask UI.

The old demo scripts (demo_lan.py, demo_comprehensive.py) that invoked
``python -m peer_discovery.network.cli`` are stale.
"""
import sys


def main():
    print(
        "peer_discovery.network.cli is deprecated after the transport "
        "consolidation. Run `python main.py` instead — it sets up the "
        "BroadcastNode and Security keystore that the discovery layer now "
        "requires.",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()

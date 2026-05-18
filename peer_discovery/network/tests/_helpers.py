"""Test helpers — fake transports + loopback pair.

After the consolidation, DiscoveryNode requires a BroadcastNode for all wire
traffic. Tests that don't want to spin up real WebSocket servers use these
fakes instead.
"""
from __future__ import annotations

import json
from typing import Any, Callable, List, Optional


class _SimpleRegistry:
    """Just enough of InMemoryRegistry / PeerRegistry to satisfy
    DiscoveryNode.lazy_register_pubkey.
    """

    def __init__(self):
        self._peers: dict[tuple[str, int], bytes] = {}

    def add_peer(self, host: str, port: int, pub_key: bytes = b"") -> None:
        self._peers[(host, port)] = pub_key

    def remove_peer(self, host: str, port: int) -> None:
        self._peers.pop((host, port), None)

    def get_peers(self):
        return list(self._peers.keys())

    def get_pub_key(self, host: str, port: int) -> bytes:
        return self._peers.get((host, port), b"")


class FakeBroadcastNode:
    """A minimal stand-in for Distribution's BroadcastNode.

    Captures every send_to_peer / broadcast in ``self.sent`` so tests can
    inspect what would have been put on the wire. ``peer_registry`` is a
    real InMemoryRegistry-shaped store so lazy_register_pubkey works.
    """

    def __init__(self, address: str = "127.0.0.1:5678"):
        self.address = address
        self.host, port_str = address.rsplit(":", 1)
        self.port = int(port_str)
        self.peer_registry = _SimpleRegistry()
        self.sent: List[tuple] = []  # ("broadcast"|"unicast", host?, port?, msg)
        self.pre_verify_hook: Optional[Callable[[Any], None]] = None
        self.on_message: Optional[Callable[[Any], None]] = None
        self.partner: Optional["FakeBroadcastNode"] = None

    def send_to_peer(self, host: str, port: int, message) -> None:
        self.sent.append(("unicast", host, port, message))
        if self.partner is not None:
            self.partner._deliver(message)

    def broadcast(self, message) -> None:
        self.sent.append(("broadcast", None, None, message))
        if self.partner is not None:
            self.partner._deliver(message)

    def _deliver(self, message) -> None:
        """Inbound side of the loopback pair: run pre_verify_hook (which
        plants the sender's pubkey for trust-on-first-use) and then call
        on_message. Skip the actual signing/verification — tests just want
        the message to traverse the abstraction.
        """
        if self.pre_verify_hook is not None:
            try:
                self.pre_verify_hook(message)
            except Exception:
                pass
        if self.on_message is not None:
            self.on_message(message)


def loopback_pair(addr_a: str = "127.0.0.1:5678",
                  addr_b: str = "127.0.0.1:5679") -> tuple[FakeBroadcastNode, FakeBroadcastNode]:
    """Two FakeBroadcastNodes pre-wired to deliver each other's messages."""
    a = FakeBroadcastNode(addr_a)
    b = FakeBroadcastNode(addr_b)
    a.partner = b
    b.partner = a
    return a, b

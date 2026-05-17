"""Configuration for the DiscoveryNode."""
from dataclasses import dataclass, field


@dataclass
class DiscoveryConfig:
    advertise_address: str
    listen_port: int
    bootstrap_peers: list[str] = field(default_factory=list)
    # The local node's public key in PEM bytes, sourced from Security's
    # ``key_store.get_public_key_pem()``. Embedded in our JOIN_REQUEST so the
    # seed records it in the membership snapshot; this is the same key
    # Distribution's ``verify()`` will look up when chat messages from us
    # arrive at other peers.
    # Default is empty bytes for tests that don't exercise the chat layer.
    public_key_pem: bytes = b""
    heartbeat_interval: float = 2.0
    tick_interval: float = 1.0
    # When True and no history handler is registered by the time start() is
    # called, DiscoveryNode installs a no-op handler that auto-promotes joiners
    # straight to ACTIVE. Lets a node run without the History team wired in.
    auto_complete_backfill: bool = True
    # WebSocket timeout used by the bootstrap client when reaching out to a
    # seed. Lower this for interactive demos so a missing seed fails fast
    # instead of hanging the user-visible "connect" request.
    bootstrap_timeout: float = 30.0

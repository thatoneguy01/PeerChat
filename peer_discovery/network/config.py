"""Configuration for the DiscoveryNode."""
from dataclasses import dataclass, field


@dataclass
class DiscoveryConfig:
    """Static configuration for a DiscoveryNode.

    After the consolidation, the discovery layer has no listening port of
    its own — all wire traffic rides Distribution's BroadcastNode. The
    ``advertise_address`` is the BroadcastNode's address (e.g. ``lan_ip:5678``)
    and is what other peers store in their membership snapshot.
    """

    # "lan_ip:chat_port" — this node's identity across the cluster.
    advertise_address: str
    # Out-of-band seed list: "host:port" strings. The host:port should be
    # the seed's BroadcastNode address (chat port). Empty list means this
    # node is the first one in the room and creates the room itself.
    bootstrap_peers: list[str] = field(default_factory=list)
    # PEM bytes of the local node's public key. Sourced from Security's
    # key_store.get_public_key_pem() and threaded through main.py →
    # chat_service.public_key_pem → here. Embedded in every outgoing
    # discovery envelope so peers can register us for chat verify().
    public_key_override: bytes | None = None
    # Heartbeats now ride Distribution's signed-and-ACK'd broadcast. 5s is
    # comfortable — each heartbeat is a real, signed message.
    heartbeat_interval: float = 5.0
    tick_interval: float = 1.0
    # When True and no history handler is registered by the time start() is
    # called, DiscoveryNode installs a no-op handler that auto-promotes
    # joiners straight to ACTIVE. Lets a node run without the History team
    # wired in (the demo path).
    auto_complete_backfill: bool = True
    # Wait timeout (seconds) for the JOIN_RESPONSE to arrive after sending
    # the JOIN_REQUEST. attempt_bootstrap blocks on threading.Event.wait()
    # for this duration before declaring the seed unreachable.
    bootstrap_timeout: float = 30.0

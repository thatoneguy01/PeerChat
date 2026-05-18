"""Configuration for the DiscoveryNode."""
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DiscoveryConfig:
    advertise_address: str
    listen_port: int
    bootstrap_peers: list[str] = field(default_factory=list)
    key_dir: Path | None = None
    heartbeat_interval: float = 2.0
    tick_interval: float = 1.0
    enable_crypto: bool = True
    # When True and no history handler is registered by the time start() is
    # called, DiscoveryNode installs a no-op handler that auto-promotes joiners
    # straight to ACTIVE. Lets a node run without the History team wired in.
    auto_complete_backfill: bool = True
    # TCP timeout used by the bootstrap client when reaching out to a seed.
    # Lower this for interactive demos so a missing seed fails fast instead
    # of hanging the user-visible "connect" request.
    bootstrap_timeout: float = 30.0
    # If set, the discovery node advertises this public key PEM in JOIN events
    # instead of generating its own keypair.  Pass in the Security module's
    # public key here so both the Distribution (signing) and Discovery
    # (identity advertisement) layers share a single cryptographic identity.
    public_key_override: bytes | None = None

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

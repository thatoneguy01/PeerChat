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

"""
Defines the PeerRegistry interface that the Discovery team must implement.

The Discovery team should subclass PeerRegistry (or duck-type it) and pass an
instance to BroadcastNode.  For local testing, InMemoryRegistry is provided here.
"""

from typing import List, Tuple
from abc import ABC, abstractmethod


class PeerRegistry(ABC):
    """
    Abstract interface for the peer list provided by the Discovery team.
    Replace InMemoryRegistry with whatever the Discovery team ships.
    """

    @abstractmethod
    def get_peers(self) -> List[Tuple[str, int]]:
        """Return the current list of known peers as (host, port) tuples."""
        ...


class InMemoryRegistry(PeerRegistry):
    """
    Minimal in-memory registry for testing and demos.
    The Discovery team will replace this with their own implementation.
    """

    def __init__(self) -> None:
        self._peers: List[Tuple[str, int, str]] = []

    def add_peer(self, host: str, port: int, pub_key: str) -> None:
        if (host, port) not in self._peers:
            self._peers.append((host, port, pub_key))

    def remove_peer(self, host: str, port: int) -> None:
        self._peers = [(h, p, pub) for h, p, pub in self._peers if (h, p) != (host, port)]

    def get_peers(self) -> List[Tuple[str, int]]:
        return list(self._peers)

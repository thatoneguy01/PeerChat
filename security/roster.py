"""In-memory public-key roster (stand-in until Peer Discovery publishes pubkeys)."""

from __future__ import annotations

from collections.abc import Mapping


class PubkeyRoster:
    """Maps user_id -> RSA public key PEM bytes."""

    def __init__(self) -> None:
        self._pubkeys: dict[str, bytes] = {}

    def register_peer(self, user_id: str, public_key_pem: bytes) -> None:
        if not user_id:
            raise ValueError("user_id must be non-empty")
        if not isinstance(public_key_pem, bytes) or not public_key_pem.strip():
            raise ValueError("public_key_pem must be non-empty bytes")
        self._pubkeys[user_id] = bytes(public_key_pem)

    def get_public_key(self, user_id: str) -> bytes | None:
        return self._pubkeys.get(user_id)

    def all_pubkeys(self) -> dict[str, bytes]:
        return dict(self._pubkeys)

    def remove_peer(self, user_id: str) -> None:
        self._pubkeys.pop(user_id, None)

    def __len__(self) -> int:
        return len(self._pubkeys)

    def __contains__(self, user_id: str) -> bool:
        return user_id in self._pubkeys

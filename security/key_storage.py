from types import MappingProxyType
from typing import Mapping

class KeyStoreError(Exception):
    pass


class MissingKeyError(KeyStoreError):
    pass


class InvalidKeyError(KeyStoreError):
    pass

class InMemoryKeyStore:
    def __init__(self):
        self._active_key_id: int | None = None
        self._keys: dict[int, bytes] = {}

    def set_active_key(self, key_id: int, key: bytes) -> None:
        if not isinstance(key_id, int) or key_id < 0:
            raise InvalidKeyError("key_id must be a non-negative integer")

        if not isinstance(key, bytes):
            raise InvalidKeyError("key must be bytes")

        if len(key) != 32:
            raise InvalidKeyError("Group key must be 32 bytes")

        self._keys[key_id] = bytes(key)
        self._active_key_id = key_id

    def get_active_key(self) -> bytes:
        if self._active_key_id is None:
            raise RuntimeError("No active group key loaded")
        return self._keys[self._active_key_id]

    def get_active_key_id(self) -> int:
        if self._active_key_id is None:
            raise RuntimeError("No active group key loaded")
        return self._active_key_id

    def clear(self) -> None:
        self._keys.clear()
        self._active_key_id = None

    def get_key(self, key_id: int) -> bytes:
        if key_id not in self._keys:
            raise MissingKeyError("Requested key is not loaded")
        return self._keys[key_id]


    def as_keyring(self) -> Mapping[int, bytes]:
        return MappingProxyType(self._keys)


    def remove_key(self, key_id: int) -> None:
        self._keys.pop(key_id, None)
        if self._active_key_id == key_id:
            self._active_key_id = None

    def __repr__(self) -> str:
        return f"InMemoryKeyStore(active_key_id={self._active_key_id}, keys=<redacted>)"
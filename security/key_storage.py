class InMemoryKeyStore:
    def __init__(self):
        self._active_key_id: int | None = None
        self._keys: dict[int, bytes] = {}

    def set_active_key(self, key_id: int, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("Group key must be 32 bytes")
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

    def __repr__(self) -> str:
        return f"InMemoryKeyStore(active_key_id={self._active_key_id}, keys=<redacted>)"
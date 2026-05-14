from __future__ import annotations

from abc import ABC, abstractmethod


class PersistentKeyStorageError(Exception):
    pass


class PersistentKeyStorage(ABC):
    @abstractmethod
    def save_group_key(self, key_id: int, key: bytes) -> None:
        pass

    @abstractmethod
    def load_group_key(self) -> tuple[int, bytes]:
        pass

    @abstractmethod
    def delete_group_key(self) -> None:
        pass


def get_platform_key_storage() -> PersistentKeyStorage | None:
    try:
        from security.keyring_key_storage import KeyringKeyStorage
        return KeyringKeyStorage()
    except ImportError:
        return None
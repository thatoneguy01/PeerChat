from __future__ import annotations

from abc import ABC, abstractmethod


class PersistentKeyStorageError(Exception):
    """
    Base exception for persistent private key storage failures.

    Raised when:
    - secure storage is unavailable
    - key loading fails
    - key saving fails
    - stored private key data is corrupted or invalid
    """
    pass


class PersistentKeyStorage(ABC):
    """
    Abstract interface for persistent private key storage

    This layer is responsible for securely saving/loading this user's private
    key across application restarts.

    Implementations may use OS secure credential storage such as:
    - Windows Credential Manager / DPAPI
    - macOS Keychain
    - Linux Secret Service / keyring backend
    """

    @abstractmethod
    def save_private_key(self, private_key: bytes) -> None:
        # Persist this user's private key securely
        pass

    @abstractmethod
    def load_private_key(self) -> bytes:
        # Load this user's private key from secure storage
        pass

    @abstractmethod
    def delete_private_key(self) -> None:
        # Delete this user's private key from secure storage
        pass


def get_platform_key_storage() -> PersistentKeyStorage | None:
    """
    Return the best available persistent key storage backend
    for the current operating system.

    Uses Python's `keyring` package for cross-platform
    secure credential storage.
    """

    try:
        from security.keyring_key_storage import KeyringKeyStorage
        return KeyringKeyStorage()
    except ImportError:
        return None
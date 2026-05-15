from __future__ import annotations

from abc import ABC, abstractmethod


class PersistentKeyStorageError(Exception):
    """
    Base exception for persistent key storage failures.

    Raised when:
    - secure storage is unavailable
    - key loading fails
    - key saving fails
    - stored data is corrupted or invalid
    """
    pass


class PersistentKeyStorage(ABC):
    """
    Abstract interface for persistent group key storage.

    This layer is responsible for securely saving/loading
    encryption keys across application restarts.

    Implementations may use:
    - OS credential stores
    - TPM-backed storage
    - macOS Keychain
    - Windows DPAPI
    - Linux Secret Service/keyring

    The encryption system interacts with this interface
    instead of depending on a specific platform backend.
    """

    @abstractmethod
    def save_group_key(self, key_id: int, key: bytes) -> None:
        """
        Persist a group key securely.

        Parameters:
            key_id:
                Integer identifier for the key version/epoch.

            key:
                AES-256 group key bytes (32 bytes).

        Raises:
            PersistentKeyStorageError:
                If the key cannot be securely stored.
        """
        pass

    @abstractmethod
    def load_group_key(self) -> tuple[int, bytes]:
        """
        Load a group key from secure storage.

        Returns:
            A tuple containing the key ID and the key bytes.

        Raises:
            PersistentKeyStorageError:
                If the key cannot be loaded.
        """
        pass

    @abstractmethod
    def delete_group_key(self) -> None:
        """
        Delete a group key from secure storage.

        Raises:
            PersistentKeyStorageError:
                If the key cannot be deleted.
        """
        pass


def get_platform_key_storage() -> PersistentKeyStorage | None:
    """
    Return the best available persistent key storage backend
    for the current operating system.

    Current implementation:
        - Uses Python's `keyring` package for cross-platform
          secure credential storage.

    OS mappings:
        Windows -> Credential Manager / DPAPI
        macOS   -> Keychain
        Linux   -> Secret Service / keyring backend

    Returns:
        PersistentKeyStorage:
            A usable persistent storage backend.

        None:
            If secure persistent storage is unavailable.
            In this case, the application falls back to
            in-memory-only key storage.
    """
    try:
        from security.keyring_key_storage import KeyringKeyStorage
        return KeyringKeyStorage()
    except ImportError:
        return None
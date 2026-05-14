from types import MappingProxyType
from typing import Mapping

class KeyStoreError(Exception):
    # Base exception for key store errors
    pass


class MissingKeyError(KeyStoreError):
    # Exception raised when a requested key is not found in the key store
    pass


class InvalidKeyError(KeyStoreError):
    # Exception raised when an invalid key is provided to the key store
    pass

class InMemoryKeyStore:
    """
    Stores active encryption keys in memory

    This class is responsible for:
    - Holding AES-256 group keys during runtime
    - Tracking the currently active key
    - Providing keys to encrypt/decrypt functions
    - Avoiding accidental exposure of secrets in logs/debuggings
    """

    def __init__(self):
        """
        Initializes an empty in-memory key store

        _active_key_id:
            tracks which key is used for encryption

        _keys:
            maps key_id to the actual key bytes
        """
        self._active_key_id: int | None = None
        self._keys: dict[int, bytes] = {}

    def set_active_key(self, key_id: int, key: bytes) -> None:
        """
        Store a key in memory and mark it as active key

        Parameters:
            - key_id: a non-negative integer identifier for the key
            - key: the key bytes to store (must be 32 bytes for AES-256)

        Raises:
            - InvalidKeyError: if key_id is not a non-negative integer
                               or if key is not valid AES-256
        """

        if not isinstance(key_id, int) or key_id < 0:
            raise InvalidKeyError("key_id must be a non-negative integer")

        if not isinstance(key, bytes):
            raise InvalidKeyError("key must be bytes")

        if len(key) != 32:
            raise InvalidKeyError("Group key must be 32 bytes")

        self._keys[key_id] = bytes(key)
        self._active_key_id = key_id

    def get_active_key(self) -> bytes:
        """
        Return the currently active group key

        Used for encryption and decryption of messages.
        
        Raises an error if no active key is loaded.
        """

        if self._active_key_id is None:
            raise RuntimeError("No active group key loaded")
        return self._keys[self._active_key_id]

    def get_active_key_id(self) -> int:
        """
        Return the ID of the currently active key

        Used for:
            - encryption metadata to indicate which key was used
            - Key rotation management

        Raises an error if no active key is loaded.
        """

        if self._active_key_id is None:
            raise RuntimeError("No active group key loaded")
        return self._active_key_id

    def clear(self) -> None:
        """
        Remove all keys from memory.

        This is a best-effort cleanup operation.

        Used when:
            - Logging out
            - Leaving the room
            - Shutting down the application
        """

        self._keys.clear()
        self._active_key_id = None

    def get_key(self, key_id: int) -> bytes:
        """
        Return a specific key by key_id.

        Used during decryption when a message references
        an older or different key version.

        Parameters:
            key_id:
                Identifier of the desired key.

        Raises:
            MissingKeyError:
                If the requested key is not loaded.
        """

        if key_id not in self._keys:
            raise MissingKeyError("Requested key is not loaded")
        return self._keys[key_id]


    def as_keyring(self) -> Mapping[int, bytes]:
        """
        Return a read-only view of all loaded keys.

        Used by decrypt_message(keyring=...) so decryption can
        select the correct group key based on key_id.
        """

        return MappingProxyType(self._keys)


    def remove_key(self, key_id: int) -> None:
        """
        Remove a specific key from memory.

        If the removed key was the active key,
        the active key reference is cleared.

        Parameters:
            key_id:
                Identifier of the key to remove.
        """

        self._keys.pop(key_id, None)
        if self._active_key_id == key_id:
            self._active_key_id = None

    def __repr__(self) -> str:
        """
        Return a safe debug representation.
        """
        
        return f"InMemoryKeyStore(active_key_id={self._active_key_id}, keys=<redacted>)"
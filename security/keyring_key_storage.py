from __future__ import annotations

import base64
import json
import keyring

from security.persistent_key_storage import PersistentKeyStorage, PersistentKeyStorageError


class KeyringKeyStorage(PersistentKeyStorage):
    """
    Cross-platform persistent key storage using the operating
    system's secure credential/key storage.

    OS mappings:
        Windows -> Credential Manager / DPAPI
        macOS   -> Keychain
        Linux   -> Secret Service / keyring backend

    Purpose:
        Persist the shared AES-256 group key across application
        restarts without storing plaintext keys directly in files.

    Notes:
        - The active key is still loaded into memory during runtime.
        - This layer only handles persistent storage.
    """

    SERVICE_NAME = "peerchat"
    ACCOUNT_NAME = "group_key"


    def save_group_key(self, key_id: int, key: bytes) -> None:
        """
        Securely save a group key into the OS keyring.

        Parameters:
            key_id:
                Integer identifier for the key version/epoch.

            key:
                AES-256 group key bytes (must be exactly 32 bytes).

        Raises:
            PersistentKeyStorageError:
                If validation fails or the key cannot be stored.
        """

        if not isinstance(key_id, int) or key_id < 0:
            raise PersistentKeyStorageError("key_id must be a non-negative integer")

        if not isinstance(key, bytes) or len(key) != 32:
            raise PersistentKeyStorageError("group key must be 32 bytes")

        payload = {
            "key_id": key_id,
            "key_b64": base64.b64encode(key).decode("ascii"),
        }

        keyring.set_password(
            self.SERVICE_NAME,
            self.ACCOUNT_NAME,
            json.dumps(payload),
        )


    def load_group_key(self) -> tuple[int, bytes]:
        """
        Load the previously stored group key from the OS keyring.

        Returns:
            tuple[int, bytes]:
                (key_id, AES group key)

        Raises:
            PersistentKeyStorageError:
                If the key is missing, corrupted, or invalid.
        """

        stored = keyring.get_password(self.SERVICE_NAME, self.ACCOUNT_NAME)

        if stored is None:
            raise PersistentKeyStorageError("no stored group key found")

        try:
            payload = json.loads(stored)
            key_id = int(payload["key_id"])
            key = base64.b64decode(payload["key_b64"])

            if len(key) != 32:
                raise ValueError("stored key is not 32 bytes")

            return key_id, key

        except Exception as exc:
            raise PersistentKeyStorageError("failed to load stored group key") from exc


    def delete_group_key(self) -> None:
        """
        Remove the stored group key from the OS keyring.

        Used when:
            - logging out
            - rotating encryption keys
            - clearing local credentials

        If the credential does not exist, the operation
        silently succeeds.
        """
        try:
            keyring.delete_password(self.SERVICE_NAME, self.ACCOUNT_NAME)
        except keyring.errors.PasswordDeleteError:
            pass
from __future__ import annotations

import base64
import json
import keyring

from security.persistent_key_storage import PersistentKeyStorage, PersistentKeyStorageError


class KeyringKeyStorage(PersistentKeyStorage):
    """
    Cross-platform persistent key storage using the OS keyring.

    Windows: Credential Manager / DPAPI
    macOS: Keychain
    Linux: Secret Service / keyring backend
    """

    SERVICE_NAME = "peerchat"
    ACCOUNT_NAME = "group_key"

    def save_group_key(self, key_id: int, key: bytes) -> None:
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
        try:
            keyring.delete_password(self.SERVICE_NAME, self.ACCOUNT_NAME)
        except keyring.errors.PasswordDeleteError:
            pass
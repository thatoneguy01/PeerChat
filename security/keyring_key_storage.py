from __future__ import annotations

import base64
import keyring

from security.persistent_key_storage import PersistentKeyStorage, PersistentKeyStorageError


class KeyringKeyStorage(PersistentKeyStorage):
    """
    Cross-platform persistent key storage using the operating
    system's secure credential/key storage

    OS mappings:
        Windows -> Credential Manager / DPAPI
        macOS   -> Keychain
        Linux   -> Secret Service / keyring backend

    Purpose:
        Persist this user's asymmetric private key across application restarts
        without storing plaintext private key material in project files

    Notes:
        - Public keys are not secret and do not need this protected storage
        - The private key is still loaded into memory during runtime
    """

    SERVICE_NAME = "peerchat"
    ACCOUNT_NAME = "private_key"


    def save_private_key(self, private_key: bytes) -> None:
        # Securely save this user's private key into the OS keyring

        if not isinstance(private_key, bytes):
            raise PersistentKeyStorageError("private key must be bytes")

        if len(private_key) == 0:
            raise PersistentKeyStorageError("private key must not be empty")

        encoded_private_key = base64.b64encode(private_key).decode("ascii")

        keyring.set_password(
            self.SERVICE_NAME,
            self.ACCOUNT_NAME,
            encoded_private_key,
        )


    def load_private_key(self) -> bytes:
        # Load this user's private key from the OS keyring.
        
        stored = keyring.get_password(
            self.SERVICE_NAME,
            self.ACCOUNT_NAME,
        )

        if stored is None:
            raise PersistentKeyStorageError("no stored private key found")

        try:
            private_key = base64.b64decode(stored)
        except Exception as exc:
            raise PersistentKeyStorageError(
                "failed to decode stored private key"
            ) from exc

        if len(private_key) == 0:
            raise PersistentKeyStorageError("stored private key is empty")

        return private_key
    

    def delete_private_key(self) -> None:
        """
        Remove this user's private key from the OS keyring.

        If the credential does not exist, the operation silently succeeds.
        """
        try:
            keyring.delete_password(
                self.SERVICE_NAME,
                self.ACCOUNT_NAME,
            )
        except keyring.errors.PasswordDeleteError:
            pass
from cryptography.hazmat.primitives import serialization

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
    Stores this node/user's private key in memory during runtime.

    This class does not persist the private key to disk. Persistent storage is
    handled by a separate backend such as KeyringPrivateKeyStorage.
    """

    def __init__(self):
        # Initialize an empty runtime private key store
        self._private_key: bytes | None = None


    def set_private_key(self, private_key: bytes) -> None:
        # Load this user's private key into memory

        if not isinstance(private_key, bytes):
            raise InvalidKeyError("private key must be bytes")

        if len(private_key) == 0:
            raise InvalidKeyError("private key must not be empty")

        self._private_key = bytes(private_key)


    def get_private_key(self) -> bytes:
        """
        Return the currently loaded private key.

        Used for decrypting messages intended for
        this user and/or signing messages as this user.
        """

        if self._private_key is None:
            raise MissingKeyError("private key is not loaded")

        return self._private_key
    

    def get_public_key_pem(self) -> bytes:
        """
        Derive and return the public key PEM from the loaded private key.

        Used by:
            - Peer Discovery for public key distribution
            - Message encryption for identifying this node's public key
            - Signature verification setup

        Raises:
            MissingKeyError:
                If no private key is currently loaded.

            InvalidKeyError:
                If the stored private key bytes cannot be parsed as PEM.
        """

        try:
            private_key = serialization.load_pem_private_key(
                self.get_private_key(),
                password=None,
            )
        except MissingKeyError:
            raise
        except Exception as exc:
            raise InvalidKeyError("failed to parse private key PEM") from exc

        public_key = private_key.public_key()

        return public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )


    def has_private_key(self) -> bool:
        # Return True if a private key is currently loaded in memory
        return self._private_key is not None

    def clear(self) -> None:
        # Remove the private key reference from memory
        self._private_key = None

    def __repr__(self) -> str:
        # Return a safe debug representation without exposing key material.
        loaded = self._private_key is not None
        return f"InMemoryKeyStore(private_key_loaded={loaded}, private_key=<redacted>)"
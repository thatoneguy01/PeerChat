from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from security.key_storage import InMemoryKeyStore
from security.persistent_key_storage import (
    PersistentKeyStorage,
    PersistentKeyStorageError,
)


def generate_rsa_private_key_pem() -> bytes:
    # Generate a new RSA private key and return it serialized as PEM bytes

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def initialize_private_key_store(
    runtime_store: InMemoryKeyStore,
    persistent_store: PersistentKeyStorage | None,
) -> bytes:
    """
    Load private key on setup

    If no private key exists in persistent storage
      - generate a new key
      - save to persistent storage
      - load into runtime store

    Returns:
        The public key PEM derived from the loaded private key
    """

    private_key_pem: bytes

    if persistent_store is None:
        private_key_pem = generate_rsa_private_key_pem()
        runtime_store.set_private_key(private_key_pem)
        return runtime_store.get_public_key_pem()

    try:
        private_key_pem = persistent_store.load_private_key()

    except PersistentKeyStorageError:
        private_key_pem = generate_rsa_private_key_pem()
        persistent_store.save_private_key(private_key_pem)

    runtime_store.set_private_key(private_key_pem)

    return runtime_store.get_public_key_pem()
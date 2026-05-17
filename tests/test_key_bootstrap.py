import pytest

from security.key_bootstrap import (
    generate_rsa_private_key_pem,
    initialize_private_key_store,
)
from security.key_storage import InMemoryKeyStore
from security.persistent_key_storage import PersistentKeyStorage, PersistentKeyStorageError


class FakePersistentKeyStorage(PersistentKeyStorage):
    def __init__(self):
        self.private_key = None

    def save_private_key(self, private_key: bytes) -> None:
        self.private_key = private_key

    def load_private_key(self) -> bytes:
        if self.private_key is None:
            raise PersistentKeyStorageError("no stored private key")
        return self.private_key

    def delete_private_key(self) -> None:
        self.private_key = None


def test_generate_rsa_private_key_pem_returns_pem_bytes():
    private_key_pem = generate_rsa_private_key_pem()

    assert isinstance(private_key_pem, bytes)
    assert b"BEGIN PRIVATE KEY" in private_key_pem


def test_initialize_loads_existing_private_key():
    runtime_store = InMemoryKeyStore()
    persistent_store = FakePersistentKeyStorage()

    existing_key = generate_rsa_private_key_pem()
    persistent_store.save_private_key(existing_key)

    public_key_pem = initialize_private_key_store(
        runtime_store,
        persistent_store,
    )

    assert runtime_store.get_private_key() == existing_key
    assert b"BEGIN PUBLIC KEY" in public_key_pem


def test_initialize_generates_and_saves_private_key_when_missing():
    runtime_store = InMemoryKeyStore()
    persistent_store = FakePersistentKeyStorage()

    public_key_pem = initialize_private_key_store(
        runtime_store,
        persistent_store,
    )

    assert runtime_store.has_private_key()
    assert persistent_store.private_key == runtime_store.get_private_key()
    assert b"BEGIN PUBLIC KEY" in public_key_pem


def test_initialize_generates_runtime_only_key_if_no_persistent_store():
    runtime_store = InMemoryKeyStore()

    public_key_pem = initialize_private_key_store(
        runtime_store,
        None,
    )

    assert runtime_store.has_private_key()
    assert b"BEGIN PUBLIC KEY" in public_key_pem
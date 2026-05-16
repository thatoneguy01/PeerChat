import pytest

from security.key_storage import InMemoryKeyStore
from security.persistent_key_storage import (
    get_platform_key_storage,
    PersistentKeyStorageError,
)


def test_keyring_private_key_storage_round_trip():
    persistent_store = get_platform_key_storage()

    if persistent_store is None:
        pytest.skip("No persistent key storage backend available")

    private_key = b"fake-private-key-bytes-for-testing"

    persistent_store.delete_private_key()
    persistent_store.save_private_key(private_key)

    loaded_private_key = persistent_store.load_private_key()

    assert loaded_private_key == private_key

    persistent_store.delete_private_key()


def test_persistent_storage_rejects_non_bytes_private_key():
    persistent_store = get_platform_key_storage()

    if persistent_store is None:
        pytest.skip("No persistent key storage backend available")

    with pytest.raises(PersistentKeyStorageError):
        persistent_store.save_private_key("not bytes")


def test_persistent_storage_rejects_empty_private_key():
    persistent_store = get_platform_key_storage()

    if persistent_store is None:
        pytest.skip("No persistent key storage backend available")

    with pytest.raises(PersistentKeyStorageError):
        persistent_store.save_private_key(b"")


def test_persistent_storage_to_runtime_store_flow():
    persistent_store = get_platform_key_storage()

    if persistent_store is None:
        pytest.skip("No persistent key storage backend available")

    runtime_store = InMemoryKeyStore()
    private_key = b"fake-private-key-bytes-for-testing"

    persistent_store.delete_private_key()
    persistent_store.save_private_key(private_key)

    loaded_private_key = persistent_store.load_private_key()
    runtime_store.set_private_key(loaded_private_key)

    assert runtime_store.get_private_key() == private_key

    runtime_store.clear()
    persistent_store.delete_private_key()


def test_delete_private_key_removes_stored_key():
    persistent_store = get_platform_key_storage()

    if persistent_store is None:
        pytest.skip("No persistent key storage backend available")

    private_key = b"fake-private-key-bytes-for-testing"

    persistent_store.save_private_key(private_key)
    persistent_store.delete_private_key()

    with pytest.raises(PersistentKeyStorageError):
        persistent_store.load_private_key()
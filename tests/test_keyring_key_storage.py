import pytest

from security.key_storage import InMemoryKeyStore
from security.persistent_key_storage import get_platform_key_storage, PersistentKeyStorageError


def test_keyring_persistent_storage_round_trip():
    persistent_store = get_platform_key_storage()

    if persistent_store is None:
        pytest.skip("No persistent key storage backend available")

    key_id = 7
    key = b"a" * 32

    persistent_store.delete_group_key()
    persistent_store.save_group_key(key_id, key)

    loaded_key_id, loaded_key = persistent_store.load_group_key()

    assert loaded_key_id == key_id
    assert loaded_key == key

    persistent_store.delete_group_key()


def test_persistent_storage_rejects_invalid_key_length():
    persistent_store = get_platform_key_storage()

    if persistent_store is None:
        pytest.skip("No persistent key storage backend available")

    with pytest.raises(PersistentKeyStorageError):
        persistent_store.save_group_key(0, b"short")


def test_persistent_storage_to_runtime_store_flow():
    persistent_store = get_platform_key_storage()

    if persistent_store is None:
        pytest.skip("No persistent key storage backend available")

    runtime_store = InMemoryKeyStore()

    key_id = 3
    key = b"b" * 32

    persistent_store.delete_group_key()
    persistent_store.save_group_key(key_id, key)

    loaded_key_id, loaded_key = persistent_store.load_group_key()
    runtime_store.set_active_key(loaded_key_id, loaded_key)

    assert runtime_store.get_active_key_id() == key_id
    assert runtime_store.get_active_key() == key

    runtime_store.clear()
    persistent_store.delete_group_key()
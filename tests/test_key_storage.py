import pytest
from types import MappingProxyType

from security.key_storage import (
    InMemoryKeyStore,
    InvalidKeyError,
    MissingKeyError,
)


def test_store_and_get_active_key():
    store = InMemoryKeyStore()
    key = b"a" * 32

    store.set_active_key(0, key)

    assert store.get_active_key_id() == 0
    assert store.get_active_key() == key


def test_rejects_wrong_key_length():
    store = InMemoryKeyStore()

    with pytest.raises(InvalidKeyError):
        store.set_active_key(0, b"short")


def test_rejects_non_bytes_key():
    store = InMemoryKeyStore()

    with pytest.raises(InvalidKeyError):
        store.set_active_key(0, "not bytes")


def test_rejects_negative_key_id():
    store = InMemoryKeyStore()

    with pytest.raises(InvalidKeyError):
        store.set_active_key(-1, b"a" * 32)


def test_missing_active_key_raises():
    store = InMemoryKeyStore()

    with pytest.raises(MissingKeyError):
        store.get_active_key()

    with pytest.raises(MissingKeyError):
        store.get_active_key_id()


def test_get_specific_key():
    store = InMemoryKeyStore()
    key0 = b"a" * 32
    key1 = b"b" * 32

    store.set_active_key(0, key0)
    store.set_active_key(1, key1)

    assert store.get_key(0) == key0
    assert store.get_key(1) == key1
    assert store.get_active_key_id() == 1


def test_missing_specific_key_raises():
    store = InMemoryKeyStore()

    with pytest.raises(MissingKeyError):
        store.get_key(99)


def test_as_keyring_is_read_only():
    store = InMemoryKeyStore()
    store.set_active_key(0, b"a" * 32)

    keyring = store.as_keyring()

    assert isinstance(keyring, MappingProxyType)
    assert keyring[0] == b"a" * 32

    with pytest.raises(TypeError):
        keyring[1] = b"b" * 32


def test_remove_key():
    store = InMemoryKeyStore()
    key = b"a" * 32

    store.set_active_key(0, key)
    store.remove_key(0)

    with pytest.raises(MissingKeyError):
        store.get_key(0)

    with pytest.raises(MissingKeyError):
        store.get_active_key()


def test_clear_removes_all_keys():
    store = InMemoryKeyStore()

    store.set_active_key(0, b"a" * 32)
    store.set_active_key(1, b"b" * 32)

    store.clear()

    with pytest.raises(MissingKeyError):
        store.get_active_key()

    with pytest.raises(MissingKeyError):
        store.get_key(0)

    with pytest.raises(MissingKeyError):
        store.get_key(1)


def test_repr_redacts_key_material():
    store = InMemoryKeyStore()
    key = b"a" * 32

    store.set_active_key(0, key)

    output = repr(store)

    assert "InMemoryKeyStore" in output
    assert "active_key_id=0" in output
    assert "keys=<redacted>" in output
    assert key.hex() not in output
    assert str(key) not in output
import pytest

from security.key_storage import (
    InMemoryKeyStore,
    InvalidKeyError,
    MissingKeyError,
)


def test_store_and_get_private_key():
    store = InMemoryKeyStore()
    private_key = b"fake-private-key-bytes"

    store.set_private_key(private_key)

    assert store.get_private_key() == private_key


def test_rejects_non_bytes_private_key():
    store = InMemoryKeyStore()

    with pytest.raises(InvalidKeyError):
        store.set_private_key("not bytes")


def test_rejects_empty_private_key():
    store = InMemoryKeyStore()

    with pytest.raises(InvalidKeyError):
        store.set_private_key(b"")


def test_missing_private_key_raises():
    store = InMemoryKeyStore()

    with pytest.raises(MissingKeyError):
        store.get_private_key()


def test_clear_removes_private_key():
    store = InMemoryKeyStore()
    private_key = b"fake-private-key-bytes"

    store.set_private_key(private_key)
    store.clear()

    with pytest.raises(MissingKeyError):
        store.get_private_key()


def test_repr_redacts_private_key_material():
    store = InMemoryKeyStore()
    private_key = b"fake-private-key-bytes"

    store.set_private_key(private_key)

    output = repr(store)

    assert "InMemoryKeyStore" in output
    assert "private_key=<redacted>" in output
    assert private_key.hex() not in output
    assert str(private_key) not in output
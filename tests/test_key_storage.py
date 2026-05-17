import pytest

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization

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


def test_get_public_key_pem_derives_public_key_from_private_key():
    store = InMemoryKeyStore()

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    private_key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    expected_public_key_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    store.set_private_key(private_key_pem)

    assert store.get_public_key_pem() == expected_public_key_pem
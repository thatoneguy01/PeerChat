"""RSA keypair generation and PEM serialization (Security team v1)."""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

RSA_KEY_SIZE = 2048
PUBLIC_KEY_ENCODING = serialization.Encoding.PEM
PRIVATE_KEY_ENCODING = serialization.Encoding.PEM
KEY_FORMAT = serialization.PrivateFormat.PKCS8
PUBLIC_FORMAT = serialization.PublicFormat.SubjectPublicKeyInfo
ENCRYPTION_ALGORITHM = serialization.NoEncryption()


def generate_rsa_keypair() -> tuple[bytes, bytes]:
    """Return (private_key_pem, public_key_pem). Generate only if none exists locally."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=RSA_KEY_SIZE)
    private_pem = private_key.private_bytes(
        encoding=PRIVATE_KEY_ENCODING,
        format=KEY_FORMAT,
        encryption_algorithm=ENCRYPTION_ALGORITHM,
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=PUBLIC_KEY_ENCODING,
        format=PUBLIC_FORMAT,
    )
    return private_pem, public_pem


def load_private_key(private_key_pem: bytes) -> RSAPrivateKey:
    return serialization.load_pem_private_key(private_key_pem, password=None)


def load_public_key(public_key_pem: bytes) -> RSAPublicKey:
    return serialization.load_pem_public_key(public_key_pem)


def public_key_pem_from_private(private_key_pem: bytes) -> bytes:
    private_key = load_private_key(private_key_pem)
    return private_key.public_key().public_bytes(
        encoding=PUBLIC_KEY_ENCODING,
        format=PUBLIC_FORMAT,
    )

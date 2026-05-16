"""Tests for CryptoProvider."""
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from peer_discovery.network.crypto_provider import (
    NullCryptoProvider,
    RSACryptoProvider,
)


@pytest.fixture
def keys():
    k1 = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    k2 = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return k1, k2


def test_null_provider():
    provider = NullCryptoProvider()
    assert provider.get_public_key_bytes() == b"null-pub-key"
    assert provider.encrypt_for(b"hello", b"any") == b"hello"
    assert provider.decrypt(b"hello") == b"hello"


def test_rsa_provider_encrypt_decrypt(keys):
    alice_priv, bob_priv = keys
    
    alice = RSACryptoProvider(alice_priv)
    bob = RSACryptoProvider(bob_priv)
    
    msg = b"secret payload larger than block size " * 100
    
    # Alice encrypts for Bob
    bob_pub = bob.get_public_key_bytes()
    ciphertext = alice.encrypt_for(msg, bob_pub)
    
    # Bob decrypts
    decrypted = bob.decrypt(ciphertext)
    assert decrypted == msg


def test_rsa_provider_decrypt_wrong_key(keys):
    alice_priv, bob_priv = keys
    
    alice = RSACryptoProvider(alice_priv)
    bob = RSACryptoProvider(bob_priv)
    
    msg = b"secret"
    
    # Alice encrypts for Alice (self)
    alice_pub = alice.get_public_key_bytes()
    ciphertext = alice.encrypt_for(msg, alice_pub)
    
    # Bob tries to decrypt
    with pytest.raises(ValueError, match="RSA decryption failed"):
        bob.decrypt(ciphertext)


def test_rsa_provider_tampered_ciphertext(keys):
    alice_priv, bob_priv = keys
    
    alice = RSACryptoProvider(alice_priv)
    bob = RSACryptoProvider(bob_priv)
    
    msg = b"secret"
    bob_pub = bob.get_public_key_bytes()
    ciphertext = bytearray(alice.encrypt_for(msg, bob_pub))
    
    # Tamper with AES ciphertext (end of buffer)
    ciphertext[-1] ^= 0x01
    
    with pytest.raises(ValueError, match="AES decryption failed"):
        bob.decrypt(bytes(ciphertext))


def test_rsa_provider_invalid_ciphertext():
    alice_priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    alice = RSACryptoProvider(alice_priv)
    
    with pytest.raises(ValueError, match="Ciphertext too short"):
        alice.decrypt(b"1")
        
    with pytest.raises(ValueError, match="Ciphertext truncated"):
        alice.decrypt(b"\xff\xffabc")

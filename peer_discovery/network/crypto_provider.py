"""Cryptography provider for message encryption and signing."""
import os
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CryptoProvider(Protocol):
    def get_public_key_bytes(self) -> bytes: ...
    def encrypt_for(self, data: bytes, target_pub_key_bytes: bytes) -> bytes: ...
    def decrypt(self, ciphertext: bytes) -> bytes: ...


class NullCryptoProvider(CryptoProvider):
    """A no-op provider for testing."""
    def get_public_key_bytes(self) -> bytes:
        return b"null-pub-key"
        
    def encrypt_for(self, data: bytes, target_pub_key_bytes: bytes) -> bytes:
        return data
        
    def decrypt(self, ciphertext: bytes) -> bytes:
        return ciphertext


class RSACryptoProvider(CryptoProvider):
    """RSA-2048 provider using AES-256-GCM for hybrid encryption."""
    
    def __init__(self, private_key: rsa.RSAPrivateKey):
        self._private_key = private_key
        self._public_key = private_key.public_key()
        self._pub_bytes = self._public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def get_public_key_bytes(self) -> bytes:
        return self._pub_bytes

    def encrypt_for(self, data: bytes, target_pub_key_bytes: bytes) -> bytes:
        target_pub = serialization.load_pem_public_key(target_pub_key_bytes)
        if not isinstance(target_pub, rsa.RSAPublicKey):
            raise ValueError("Target public key must be RSA")
            
        # 1. Generate AES-256-GCM key and nonce
        aes_key = AESGCM.generate_key(bit_length=256)
        aesgcm = AESGCM(aes_key)
        nonce = os.urandom(12)
        
        # 2. Encrypt payload with AES-GCM
        ciphertext = aesgcm.encrypt(nonce, data, None)
        
        # 3. Encrypt AES key + nonce with RSA
        # RSA-OAEP with SHA-256
        key_material = aes_key + nonce  # 32 + 12 = 44 bytes
        encrypted_key_material = target_pub.encrypt(
            key_material,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None
            )
        )
        
        # Format: [2-byte RSA block len] + [RSA block] + [AES ciphertext]
        rsa_len = len(encrypted_key_material)
        return rsa_len.to_bytes(2, "big") + encrypted_key_material + ciphertext

    def decrypt(self, ciphertext: bytes) -> bytes:
        if len(ciphertext) < 2:
            raise ValueError("Ciphertext too short")
            
        rsa_len = int.from_bytes(ciphertext[:2], "big")
        if len(ciphertext) < 2 + rsa_len:
            raise ValueError("Ciphertext truncated")
            
        encrypted_key_material = ciphertext[2:2+rsa_len]
        aes_ciphertext = ciphertext[2+rsa_len:]
        
        # 1. Decrypt AES key + nonce
        try:
            key_material = self._private_key.decrypt(
                encrypted_key_material,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None
                )
            )
        except ValueError as e:
            raise ValueError(f"RSA decryption failed: {e}")
            
        if len(key_material) != 44:
            raise ValueError("Invalid key material length")
            
        aes_key = key_material[:32]
        nonce = key_material[32:]
        
        # 2. Decrypt payload
        aesgcm = AESGCM(aes_key)
        try:
            return aesgcm.decrypt(nonce, aes_ciphertext, None)
        except Exception as e:
            raise ValueError(f"AES decryption failed: {e}")

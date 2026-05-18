"""Cryptography provider for message encryption and signing."""
import logging
import os
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)


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


class PassthroughCryptoProvider(CryptoProvider):
    """Advertises an externally-supplied public key (e.g. from the Security
    module) in JOIN events so all peers learn the correct key for message
    verification.  Encryption is a no-op — actual message crypto is owned by
    the Security team and handled in the Distribution layer.
    """

    def __init__(self, public_key_bytes: bytes):
        self._pub_bytes = public_key_bytes

    def get_public_key_bytes(self) -> bytes:
        return self._pub_bytes

    def encrypt_for(self, data: bytes, target_pub_key_bytes: bytes) -> bytes:
        return data  # no-op: encryption is the Security module's responsibility

    def decrypt(self, ciphertext: bytes) -> bytes:
        return ciphertext  # no-op


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
            logger.warning("decrypt_failed reason=ciphertext_too_short len=%d", len(ciphertext))
            raise ValueError("Ciphertext too short")

        rsa_len = int.from_bytes(ciphertext[:2], "big")
        if len(ciphertext) < 2 + rsa_len:
            logger.warning(
                "decrypt_failed reason=truncated rsa_len=%d total_len=%d",
                rsa_len, len(ciphertext),
            )
            raise ValueError("Ciphertext truncated")

        encrypted_key_material = ciphertext[2:2+rsa_len]
        aes_ciphertext = ciphertext[2+rsa_len:]

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
            logger.warning(
                "decrypt_failed reason=rsa_oaep_failed rsa_bytes=%d err=%s — likely "
                "encrypted with a different public key than ours",
                rsa_len, e,
            )
            raise ValueError(f"RSA decryption failed: {e}")

        if len(key_material) != 44:
            logger.warning(
                "decrypt_failed reason=bad_key_material_length got=%d expected=44",
                len(key_material),
            )
            raise ValueError("Invalid key material length")

        aes_key = key_material[:32]
        nonce = key_material[32:]

        aesgcm = AESGCM(aes_key)
        try:
            plaintext = aesgcm.decrypt(nonce, aes_ciphertext, None)
            logger.debug(
                "decrypt_ok plaintext_bytes=%d rsa_bytes=%d aes_bytes=%d",
                len(plaintext), rsa_len, len(aes_ciphertext),
            )
            return plaintext
        except Exception as e:
            logger.warning(
                "decrypt_failed reason=aes_gcm_failed aes_bytes=%d err=%s — tag "
                "mismatch usually means ciphertext was modified in transit",
                len(aes_ciphertext), e,
            )
            raise ValueError(f"AES decryption failed: {e}")

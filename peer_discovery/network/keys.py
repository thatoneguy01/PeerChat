"""Persistent key generation and loading."""
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def get_default_key_dir() -> Path:
    """Get the default key directory (~/.peerchat/keys)."""
    home = Path.home()
    return home / ".peerchat" / "keys"


def generate_or_load_keypair(key_dir: Path | None = None) -> rsa.RSAPrivateKey:
    """Load the RSA private key from disk, generating it if it doesn't exist."""
    if key_dir is None:
        key_dir = get_default_key_dir()
        
    key_dir.mkdir(parents=True, exist_ok=True)
    priv_path = key_dir / "id_rsa"
    
    if priv_path.exists():
        with open(priv_path, "rb") as f:
            return serialization.load_pem_private_key(
                f.read(),
                password=None,
            )
            
    # Generate new key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    
    # Save with restricted permissions
    priv_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()
    )
    
    # Write atomically
    tmp_path = priv_path.with_suffix(".tmp")
    with open(tmp_path, "wb") as f:
        f.write(priv_bytes)
    os.chmod(tmp_path, 0o600)
    tmp_path.rename(priv_path)
    
    return private_key

"""Tests for key management."""
import os

from cryptography.hazmat.primitives.asymmetric import rsa

from peer_discovery.network.keys import generate_or_load_keypair


def test_generate_and_load_keypair(tmp_path):
    key_dir = tmp_path / "keys"
    
    # First call should generate
    priv1 = generate_or_load_keypair(key_dir)
    assert isinstance(priv1, rsa.RSAPrivateKey)
    assert (key_dir / "id_rsa").exists()
    
    # Permissions should be restricted
    stat = os.stat(key_dir / "id_rsa")
    assert stat.st_mode & 0o777 == 0o600
    
    # Second call should load
    priv2 = generate_or_load_keypair(key_dir)
    
    # Keys should match
    num1 = priv1.private_numbers()
    num2 = priv2.private_numbers()
    assert num1.p == num2.p
    assert num1.q == num2.q

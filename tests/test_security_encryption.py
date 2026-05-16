"""Tests for RSA hybrid per-recipient encryption."""

from __future__ import annotations

import json

import pytest

from security.encryption import (
    DecryptPayloadError,
    EncryptionError,
    decrypt_broadcast_content,
    decrypt_from_peer,
    encrypt_broadcast_content,
    encrypt_for_peer,
    encrypt_for_peer_rsa_only,
    decrypt_from_peer_rsa_only,
    is_encrypted_content,
)
from security.rsa_keys import generate_rsa_keypair


@pytest.fixture
def alice_keys():
    return generate_rsa_keypair()


@pytest.fixture
def bob_keys():
    return generate_rsa_keypair()


class TestHybridPeerEncryption:
    def test_round_trip(self, alice_keys, bob_keys):
        _alice_priv, alice_pub = alice_keys
        bob_priv, bob_pub = bob_keys

        plaintext = b"hello from alice"
        blob = encrypt_for_peer(plaintext=plaintext, recipient_public_key_pem=bob_pub)
        assert decrypt_from_peer(ciphertext=blob, private_key_pem=bob_priv) == plaintext

    def test_wrong_private_key_fails(self, alice_keys, bob_keys):
        alice_priv, alice_pub = alice_keys
        _bob_priv, bob_pub = bob_keys

        blob = encrypt_for_peer(plaintext=b"secret", recipient_public_key_pem=bob_pub)
        with pytest.raises(DecryptPayloadError):
            decrypt_from_peer(ciphertext=blob, private_key_pem=alice_priv)

    def test_tamper_fails(self, bob_keys):
        bob_priv, bob_pub = bob_keys
        blob = bytearray(
            encrypt_for_peer(plaintext=b"x", recipient_public_key_pem=bob_pub)
        )
        blob[-1] ^= 0xFF
        with pytest.raises(DecryptPayloadError):
            decrypt_from_peer(ciphertext=bytes(blob), private_key_pem=bob_priv)


class TestBroadcastWireFormat:
    def test_encrypt_decrypt_broadcast(self, alice_keys, bob_keys):
        alice_priv, alice_pub = alice_keys
        bob_priv, bob_pub = bob_keys

        pubkeys = {"alice": alice_pub, "bob": bob_pub}
        content = encrypt_broadcast_content(
            plaintext="team chat",
            recipient_pubkeys=pubkeys,
        )
        assert is_encrypted_content(content)

        assert (
            decrypt_broadcast_content(
                content=content,
                own_user_id="bob",
                private_key_pem=bob_priv,
            )
            == "team chat"
        )
        assert (
            decrypt_broadcast_content(
                content=content,
                own_user_id="alice",
                private_key_pem=alice_priv,
            )
            == "team chat"
        )

    def test_missing_box_raises(self, bob_keys):
        bob_priv, bob_pub = bob_keys
        content = encrypt_broadcast_content(
            plaintext="hi",
            recipient_pubkeys={"bob": bob_pub},
        )
        with pytest.raises(DecryptPayloadError):
            decrypt_broadcast_content(
                content=content,
                own_user_id="alice",
                private_key_pem=bob_priv,
            )


class TestRsaOnlyMode:
    def test_short_message_round_trip(self, bob_keys):
        bob_priv, bob_pub = bob_keys
        pt = b"tiny"
        ct = encrypt_for_peer_rsa_only(plaintext=pt, recipient_public_key_pem=bob_pub)
        assert decrypt_from_peer_rsa_only(ciphertext=ct, private_key_pem=bob_priv) == pt

    def test_large_message_rejected(self, bob_keys):
        _, bob_pub = bob_keys
        with pytest.raises(EncryptionError):
            encrypt_for_peer_rsa_only(
                plaintext=b"x" * 200,
                recipient_public_key_pem=bob_pub,
            )

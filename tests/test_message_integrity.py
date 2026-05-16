"""Tests for RSA-PSS message signatures."""

from distribution import Message
from security.message_integrity import sign_message, verify_message
from security.rsa_keys import generate_rsa_keypair


def test_sign_and_verify_round_trip():
    priv, pub = generate_rsa_keypair()
    msg = Message(content='{"v":"pcrsa-h1","boxes":{}}', sender="127.0.0.1:1")
    sign_message(msg, priv)
    assert verify_message(msg, pub)


def test_tampered_content_fails_verify():
    priv, pub = generate_rsa_keypair()
    msg = Message(content="original", sender="127.0.0.1:1")
    sign_message(msg, priv)
    msg.content = "tampered"
    assert not verify_message(msg, pub)


def test_wrong_pubkey_fails_verify():
    priv, pub = generate_rsa_keypair()
    _other_priv, other_pub = generate_rsa_keypair()
    msg = Message(content="x", sender="127.0.0.1:1")
    sign_message(msg, priv)
    assert not verify_message(msg, other_pub)

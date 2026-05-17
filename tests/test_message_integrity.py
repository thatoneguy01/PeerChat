import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from distribution import Message
from security import message_integrity


SENDER = "127.0.0.1:5001"


@pytest.fixture(autouse=True)
def clear_keys():
    message_integrity.clear_keys()
    yield
    message_integrity.clear_keys()


@pytest.fixture
def keypair():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


def make_message() -> Message:
    return Message(
        id="msg-1",
        sender=SENDER,
        timestamp=12345.5,
        content="hello",
        ttl=10,
        vector_clock={SENDER: 1},
    )


def test_sign_and_verify_round_trip(keypair):
    private_pem, public_pem = keypair
    message_integrity.configure_private_key(private_pem)
    message_integrity.register_public_key(SENDER, public_pem)

    msg = message_integrity.sign(make_message())

    assert msg.signature
    assert message_integrity.verify(msg)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", "msg-2"),
        ("sender", "127.0.0.1:5002"),
        ("timestamp", 12346.0),
        ("content", "tampered"),
    ],
)
def test_verify_rejects_signed_field_tampering(keypair, field, value):
    private_pem, public_pem = keypair
    message_integrity.configure_private_key(private_pem)
    message_integrity.register_public_key(SENDER, public_pem)
    msg = message_integrity.sign(make_message())

    setattr(msg, field, value)

    assert not message_integrity.verify(msg)


def test_verify_allows_distribution_mutated_fields(keypair):
    private_pem, public_pem = keypair
    message_integrity.configure_private_key(private_pem)
    message_integrity.register_public_key(SENDER, public_pem)
    msg = message_integrity.sign(make_message())

    msg.ttl = 3
    msg.vector_clock = {SENDER: 99}

    assert message_integrity.verify(msg)


def test_verify_rejects_bad_key_or_bad_signature(keypair):
    private_pem, _public_pem = keypair
    wrong_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    wrong_public_pem = wrong_private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    message_integrity.configure_private_key(private_pem)
    message_integrity.register_public_key(SENDER, wrong_public_pem)

    msg = message_integrity.sign(make_message())
    assert not message_integrity.verify(msg)

    msg.signature = "not-base64"
    assert not message_integrity.verify(msg)

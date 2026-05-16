"""Unit tests for message encryption (design doc §14)."""

from __future__ import annotations

import base64
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from crypto.constants import (
    AES_KEY_SIZE,
    ENVELOPE_VERSION,
    HEADER_SIZE,
    MAGIC,
    MAX_PLAINTEXT_BYTES,
    NONCE_SIZE,
)
from crypto.encrypt import decrypt_message, encrypt_message
from crypto.envelope import build_aad, pack_envelope, unpack_envelope
from crypto.errors import (
    DecryptFailedError,
    InvalidEnvelopeError,
    PlaintextTooLargeError,
    UnknownSuiteError,
    UnsupportedEnvelopeVersionError,
)
from crypto.keys import (
    GroupKeyRing,
    derive_key_from_password,
    derive_key_from_psk,
    generate_salt,
)

GOLDEN_KEY = bytes(range(32))
GOLDEN_NONCE = bytes(12)
GOLDEN_PLAINTEXT = b"hello peerchat"
GOLDEN_ROOM_ID = 0


def _golden_aad() -> bytes:
    return build_aad(room_id=GOLDEN_ROOM_ID, envelope_version=ENVELOPE_VERSION)


def _golden_ciphertext_with_tag() -> bytes:
    return AESGCM(GOLDEN_KEY).encrypt(GOLDEN_NONCE, GOLDEN_PLAINTEXT, _golden_aad())


GOLDEN_ENVELOPE = pack_envelope(
    key_id=0,
    nonce=GOLDEN_NONCE,
    ciphertext_with_tag=_golden_ciphertext_with_tag(),
)


class TestGoldenVector:
    def test_fixed_encrypt_produces_known_envelope(self):
        with patch("crypto.encrypt.secrets.token_bytes", return_value=GOLDEN_NONCE):
            envelope = encrypt_message(
                key=GOLDEN_KEY,
                key_id=0,
                room_id=GOLDEN_ROOM_ID,
                plaintext=GOLDEN_PLAINTEXT,
            )
        assert envelope == GOLDEN_ENVELOPE

    def test_golden_decrypt(self):
        plaintext = decrypt_message(
            keyring={0: GOLDEN_KEY},
            room_id=GOLDEN_ROOM_ID,
            envelope=GOLDEN_ENVELOPE,
        )
        assert plaintext == GOLDEN_PLAINTEXT

    def test_header_layout(self):
        assert GOLDEN_ENVELOPE[0] == MAGIC
        assert GOLDEN_ENVELOPE[1] == ENVELOPE_VERSION
        assert GOLDEN_ENVELOPE[2] == 0x01
        assert len(GOLDEN_ENVELOPE) == HEADER_SIZE + len(_golden_ciphertext_with_tag())


class TestRoundTrip:
    @pytest.mark.parametrize("size", [0, 1, 1024, MAX_PLAINTEXT_BYTES - 1])
    def test_round_trip_sizes(self, size: int):
        key = GOLDEN_KEY
        plaintext = b"x" * size
        envelope = encrypt_message(key=key, plaintext=plaintext)
        assert decrypt_message(keyring={0: key}, envelope=envelope) == plaintext

    def test_key_id_nonzero(self):
        key = GOLDEN_KEY
        envelope = encrypt_message(key=key, key_id=7, plaintext=b"epoch")
        kid, _, _, _ = unpack_envelope(envelope)
        assert kid == 7
        assert decrypt_message(keyring={7: key}, envelope=envelope) == b"epoch"


class TestDecryptFailures:
    def test_wrong_key(self):
        envelope = encrypt_message(key=GOLDEN_KEY, plaintext=b"secret")
        with pytest.raises(DecryptFailedError):
            decrypt_message(keyring={0: bytes(32)}, envelope=envelope)

    def test_missing_key_id(self):
        envelope = encrypt_message(key=GOLDEN_KEY, key_id=3, plaintext=b"x")
        with pytest.raises(DecryptFailedError):
            decrypt_message(keyring={0: GOLDEN_KEY}, envelope=envelope)

    def test_tamper_ciphertext(self):
        envelope = bytearray(encrypt_message(key=GOLDEN_KEY, plaintext=b"x"))
        envelope[-1] ^= 0xFF
        with pytest.raises(DecryptFailedError):
            decrypt_message(keyring={0: GOLDEN_KEY}, envelope=bytes(envelope))

    def test_tamper_tag_region(self):
        envelope = bytearray(encrypt_message(key=GOLDEN_KEY, plaintext=b"x"))
        envelope[-5] ^= 0x01
        with pytest.raises(DecryptFailedError):
            decrypt_message(keyring={0: GOLDEN_KEY}, envelope=bytes(envelope))

    def test_mismatched_room_id_aad(self):
        envelope = encrypt_message(
            key=GOLDEN_KEY, room_id=0, plaintext=b"room-bound"
        )
        with pytest.raises(DecryptFailedError):
            decrypt_message(keyring={0: GOLDEN_KEY}, room_id=1, envelope=envelope)


class TestEnvelopeParsing:
    def test_bad_magic(self):
        bad = bytearray(GOLDEN_ENVELOPE)
        bad[0] = 0x00
        with pytest.raises(InvalidEnvelopeError):
            unpack_envelope(bytes(bad))

    def test_unsupported_version(self):
        bad = bytearray(GOLDEN_ENVELOPE)
        bad[1] = 0x99
        with pytest.raises(UnsupportedEnvelopeVersionError):
            unpack_envelope(bytes(bad))

    def test_unknown_suite(self):
        bad = bytearray(GOLDEN_ENVELOPE)
        bad[2] = 0x99
        with pytest.raises(UnknownSuiteError):
            unpack_envelope(bytes(bad))

    def test_envelope_too_short(self):
        with pytest.raises(InvalidEnvelopeError):
            unpack_envelope(b"\x4d\x01\x01\x00\x00\x00\x00\x00")

    def test_plaintext_too_large(self):
        with pytest.raises(PlaintextTooLargeError):
            encrypt_message(key=GOLDEN_KEY, plaintext=b"x" * (MAX_PLAINTEXT_BYTES + 1))


class TestKeyDerivation:
    def test_password_derivation_deterministic(self):
        salt = b"\x01" * 16
        k1 = derive_key_from_password("network-pass", salt=salt)
        k2 = derive_key_from_password("network-pass", salt=salt)
        assert k1 == k2
        assert len(k1) == AES_KEY_SIZE

    def test_psk_hkdf_deterministic(self):
        salt = b"peerchat-salt-v1"
        k1 = derive_key_from_psk(b"raw-secret", salt=salt)
        k2 = derive_key_from_psk(b"raw-secret", salt=salt)
        assert k1 == k2
        assert len(k1) == AES_KEY_SIZE

    def test_generate_salt_unique(self):
        assert generate_salt() != generate_salt()


class TestGroupKeyRing:
    def test_store_and_lookup(self):
        ring = GroupKeyRing({0: GOLDEN_KEY, 1: bytes(32)})
        assert ring[0] == GOLDEN_KEY
        ring[2] = bytes([2] * 32)
        assert len(ring) == 3


class TestWireHelpers:
    def test_encrypt_decrypt_content_roundtrip(self):
        from crypto.wire import decrypt_content, encrypt_content, is_encrypted_content

        key = GOLDEN_KEY
        wire = encrypt_content(key=key, plaintext="hi team")
        assert is_encrypted_content(wire)
        assert decrypt_content(keyring={0: key}, content=wire) == "hi team"

    def test_legacy_cleartext_passthrough(self):
        from crypto.wire import decrypt_content

        assert decrypt_content(keyring={0: GOLDEN_KEY}, content="plain text") == "plain text"


class TestDistributionStub:
    """Contract: distribution serializes envelope unchanged."""

    def test_json_roundtrip_preserves_envelope(self):
        import json

        envelope = encrypt_message(key=GOLDEN_KEY, plaintext=b"on the wire")
        payload = {"content_b64": base64.b64encode(envelope).decode("ascii")}
        raw = json.dumps(payload)
        restored = base64.b64decode(json.loads(raw)["content_b64"])
        assert decrypt_message(keyring={0: GOLDEN_KEY}, envelope=restored) == b"on the wire"

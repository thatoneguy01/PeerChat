"""Tests for network protocol codec."""
import pytest
from peer_discovery.network.protocol import (
    MessageType,
    NetworkMessage,
    ProtocolError,
    decode_message,
    encode_message,
)


def test_encode_decode_round_trip():
    msg = NetworkMessage(
        message_type=MessageType.JOIN_REQUEST,
        sender_id="alice",
        payload={"key": "value", "nested": {"a": 1}}
    )
    
    encoded = encode_message(msg)
    assert isinstance(encoded, str)

    decoded = decode_message(encoded)
    assert decoded.message_type == MessageType.JOIN_REQUEST
    assert decoded.sender_id == "alice"
    assert decoded.payload == {"key": "value", "nested": {"a": 1}}


def test_decode_invalid_json():
    with pytest.raises(ProtocolError, match="Failed to decode"):
        decode_message(b"{invalid json}")


def test_decode_missing_fields():
    with pytest.raises(ProtocolError, match="Missing required fields"):
        decode_message(b'{"type": "JOIN_REQUEST", "sender_id": "alice"}')


def test_decode_invalid_type():
    with pytest.raises(ProtocolError, match="Failed to decode"):
        decode_message(b'{"type": "UNKNOWN_TYPE", "sender_id": "a", "payload": {}}')


def test_decode_non_dict_payload():
    with pytest.raises(ProtocolError, match="Payload must be a JSON object"):
        decode_message(b'{"type": "HEARTBEAT", "sender_id": "a", "payload": "not a dict"}')

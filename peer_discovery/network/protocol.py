"""Network protocol definitions and codecs."""
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any


class MessageType(Enum):
    JOIN_REQUEST = "JOIN_REQUEST"
    JOIN_RESPONSE = "JOIN_RESPONSE"
    EVENT_BROADCAST = "EVENT_BROADCAST"
    SNAPSHOT_SYNC = "SNAPSHOT_SYNC"
    HEARTBEAT = "HEARTBEAT"


@dataclass
class NetworkMessage:
    message_type: MessageType
    sender_id: str
    payload: dict[str, Any]


class ProtocolError(Exception):
    """Raised for protocol encoding/decoding errors."""
    pass


def encode_message(msg: NetworkMessage) -> bytes:
    """Encode a NetworkMessage to UTF-8 JSON bytes."""
    try:
        data = {
            "type": msg.message_type.value,
            "sender_id": msg.sender_id,
            "payload": msg.payload,
        }
        return json.dumps(data).encode("utf-8")
    except Exception as e:
        raise ProtocolError(f"Failed to encode message: {e}")


def decode_message(data: bytes) -> NetworkMessage:
    """Decode UTF-8 JSON bytes to a NetworkMessage."""
    try:
        raw = json.loads(data.decode("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("Message must be a JSON object")
        
        msg_type = raw.get("type")
        sender_id = raw.get("sender_id")
        payload = raw.get("payload")
        
        if not msg_type or not sender_id or payload is None:
            raise ValueError("Missing required fields (type, sender_id, payload)")
            
        if not isinstance(payload, dict):
            raise ValueError("Payload must be a JSON object")
            
        return NetworkMessage(
            message_type=MessageType(msg_type),
            sender_id=sender_id,
            payload=payload,
        )
    except Exception as e:
        raise ProtocolError(f"Failed to decode message: {e}")

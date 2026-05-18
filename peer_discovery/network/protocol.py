"""Wire-protocol codecs for peer discovery.

After the Distribution consolidation, peer_discovery has no socket of its
own. Every discovery message — JOIN_REQUEST, JOIN_RESPONSE, GOSSIP,
HEARTBEAT — rides inside Distribution's ``Message.content`` as a small
JSON envelope. The four subtype constants below name those envelopes.

Two layers live in this module:

1. **Discovery envelope (current)** — ``encode_discovery_envelope`` /
   ``decode_discovery_envelope`` / ``is_discovery_message``. These are the
   only codecs used on the wire today. The envelope always carries the
   sender's public-key PEM so the receiver can lazy-register it for
   signature verification (trust-on-first-use).

2. **Legacy NetworkMessage (deprecated)** — ``NetworkMessage`` and the
   ``encode_message`` / ``decode_message`` pair were the wire format for
   the pre-consolidation peer_discovery TCP listener. The transport is
   gone but the codecs are retained so the older membership unit tests
   continue to pass without rewriting their fixtures.
"""
import base64
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


def encode_message(msg: NetworkMessage) -> str:
    """Encode a NetworkMessage to a JSON string.

    The WebSocket transport sends these as TEXT frames, which are str.
    """
    try:
        data = {
            "type": msg.message_type.value,
            "sender_id": msg.sender_id,
            "payload": msg.payload,
        }
        return json.dumps(data)
    except Exception as e:
        raise ProtocolError(f"Failed to encode message: {e}")


def decode_message(data: str | bytes) -> NetworkMessage:
    """Decode a JSON message string (or bytes) to a NetworkMessage.

    Accepts both str and bytes for robustness — the websockets library
    typically gives us str for TEXT frames but bytes for BINARY frames.
    """
    try:
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        raw = json.loads(data)
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


# ---------------------------------------------------------------------------
# Discovery envelope (new — used to ride inside Distribution's Message.content)
# ---------------------------------------------------------------------------

DISCOVERY_TYPE_PREFIX = "discovery_"

# Discovery subtypes carried inside Distribution's Message.content envelope.
SUBTYPE_JOIN_REQUEST = "discovery_join_request"
SUBTYPE_JOIN_RESPONSE = "discovery_join_response"
SUBTYPE_GOSSIP = "discovery_gossip"
SUBTYPE_HEARTBEAT = "discovery_heartbeat"


def is_discovery_message(content: str) -> bool:
    """Fast sniff: is this string a discovery envelope?

    Used in chat_service.message_received to decide whether to route the
    Message into the discovery dispatcher or fall through to chat handling.
    Cheap because most chat messages won't contain the literal substring.
    """
    if not isinstance(content, str):
        return False
    # JSON-encoded discovery envelopes always have the type field early.
    # The check is intentionally loose; the real validation happens in
    # decode_discovery_envelope.
    return f'"type": "{DISCOVERY_TYPE_PREFIX}' in content or \
        f'"type":"{DISCOVERY_TYPE_PREFIX}' in content


def encode_discovery_envelope(
    subtype: str,
    sender_pub_pem: bytes,
    payload: dict[str, Any],
) -> str:
    """Wrap a discovery payload as a JSON string suitable for
    ``Message.content``.

    The pubkey is included on every message so the receiver's pre-verify
    hook can lazy-register it (trust-on-first-use bootstrap).
    """
    if not subtype.startswith(DISCOVERY_TYPE_PREFIX):
        raise ProtocolError(
            f"discovery subtype {subtype!r} must start with "
            f"{DISCOVERY_TYPE_PREFIX!r}"
        )
    body = {
        "type": subtype,
        "sender_public_key_pem_b64": base64.b64encode(
            sender_pub_pem or b""
        ).decode(),
        "payload": payload,
    }
    return json.dumps(body)


def decode_discovery_envelope(content: str) -> tuple[str, bytes, dict[str, Any]]:
    """Unwrap a discovery envelope.

    Returns ``(subtype, sender_pub_pem_bytes, payload_dict)``.
    Raises ``ProtocolError`` on malformed input.
    """
    try:
        raw = json.loads(content)
    except Exception as e:
        raise ProtocolError(f"discovery envelope not valid JSON: {e}")

    if not isinstance(raw, dict):
        raise ProtocolError("discovery envelope must be a JSON object")

    subtype = raw.get("type")
    pub_b64 = raw.get("sender_public_key_pem_b64", "")
    payload = raw.get("payload", {})

    if not isinstance(subtype, str) or not subtype.startswith(DISCOVERY_TYPE_PREFIX):
        raise ProtocolError(f"not a discovery envelope: type={subtype!r}")

    if not isinstance(payload, dict):
        raise ProtocolError("discovery payload must be a JSON object")

    try:
        sender_pub = base64.b64decode(pub_b64) if pub_b64 else b""
    except Exception as e:
        raise ProtocolError(f"sender_public_key_pem_b64 not valid base64: {e}")

    return subtype, sender_pub, payload

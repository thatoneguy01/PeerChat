"""Tests for WebSocket transport layer."""
import time

from peer_discovery.network.protocol import MessageType, NetworkMessage
from peer_discovery.network.transport import WebSocketClient, WebSocketListener


def test_transport_loopback():
    received_messages = []

    def handler(ip: str, msg: NetworkMessage) -> NetworkMessage | None:
        received_messages.append((ip, msg))
        if msg.message_type == MessageType.JOIN_REQUEST:
            return NetworkMessage(
                message_type=MessageType.JOIN_RESPONSE,
                sender_id="server",
                payload={"status": "ok"},
            )
        return None

    listener = WebSocketListener("127.0.0.1", 0, handler)
    listener.start()

    try:
        host, port = listener.bound_address
        client = WebSocketClient(timeout=2.0)

        # Test 1: request expecting a response
        req1 = NetworkMessage(
            message_type=MessageType.JOIN_REQUEST,
            sender_id="alice",
            payload={"key": "val"},
        )
        resp1 = client.send_and_receive(host, port, req1)

        assert resp1 is not None
        assert resp1.message_type == MessageType.JOIN_RESPONSE
        assert resp1.payload == {"status": "ok"}

        # Wait a moment for the server thread to log/append
        time.sleep(0.1)
        assert len(received_messages) == 1
        assert received_messages[0][0] == "127.0.0.1"
        assert received_messages[0][1].sender_id == "alice"

        # Test 2: request expecting no response
        req2 = NetworkMessage(
            message_type=MessageType.HEARTBEAT,
            sender_id="alice",
            payload={},
        )
        resp2 = client.send_and_receive(host, port, req2)

        # When the handler returns None, the server closes the connection
        # before sending a frame. send_and_receive returns None.
        assert resp2 is None

        time.sleep(0.1)
        assert len(received_messages) == 2
        assert received_messages[1][1].message_type == MessageType.HEARTBEAT

    finally:
        listener.stop()


def test_transport_timeout():
    """A slow handler that takes longer than the client's recv timeout should
    result in the client returning None (TimeoutError is caught internally
    and surfaced as ``None`` per WebSocketClient.send_and_receive contract).
    """
    def slow_handler(ip: str, msg: NetworkMessage) -> NetworkMessage | None:
        time.sleep(2.0)
        return NetworkMessage(
            message_type=MessageType.JOIN_RESPONSE,
            sender_id="server",
            payload={"status": "late"},
        )

    listener = WebSocketListener("127.0.0.1", 0, slow_handler)
    listener.start()

    try:
        host, port = listener.bound_address
        client = WebSocketClient(timeout=0.5)  # 0.5s recv timeout

        req = NetworkMessage(
            message_type=MessageType.JOIN_REQUEST,
            sender_id="alice",
            payload={},
        )

        resp = client.send_and_receive(host, port, req)
        assert resp is None  # handler too slow; client gave up
    finally:
        listener.stop()

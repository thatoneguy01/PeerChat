"""Tests for TCP transport layer."""
import socket
import threading
import time
import pytest

from peer_discovery.network.protocol import MessageType, NetworkMessage
from peer_discovery.network.transport import TCPClient, TCPListener


def test_transport_loopback():
    received_messages = []
    
    def handler(ip: str, msg: NetworkMessage) -> NetworkMessage | None:
        received_messages.append((ip, msg))
        if msg.message_type == MessageType.JOIN_REQUEST:
            return NetworkMessage(
                message_type=MessageType.JOIN_RESPONSE,
                sender_id="server",
                payload={"status": "ok"}
            )
        return None

    listener = TCPListener("127.0.0.1", 0, handler)
    listener.start()
    
    try:
        host, port = listener.bound_address
        client = TCPClient(timeout=1.0)
        
        # Test 1: Send request expecting a response
        req1 = NetworkMessage(
            message_type=MessageType.JOIN_REQUEST,
            sender_id="alice",
            payload={"key": "val"}
        )
        resp1 = client.send_and_receive(host, port, req1)
        
        assert resp1 is not None
        assert resp1.message_type == MessageType.JOIN_RESPONSE
        assert resp1.payload == {"status": "ok"}
        
        # Wait a moment for async handler to finish appending
        time.sleep(0.1)
        assert len(received_messages) == 1
        assert received_messages[0][0] == "127.0.0.1"
        assert received_messages[0][1].sender_id == "alice"
        
        # Test 2: Send request expecting no response
        req2 = NetworkMessage(
            message_type=MessageType.HEARTBEAT,
            sender_id="alice",
            payload={}
        )
        resp2 = client.send_and_receive(host, port, req2)
        
        assert resp2 is None
        
        time.sleep(0.1)
        assert len(received_messages) == 2
        assert received_messages[1][1].message_type == MessageType.HEARTBEAT
        
    finally:
        listener.stop()


def test_transport_timeout():
    def slow_handler(ip: str, msg: NetworkMessage) -> NetworkMessage | None:
        time.sleep(2.0)
        return None

    listener = TCPListener("127.0.0.1", 0, slow_handler)
    listener.start()
    
    try:
        host, port = listener.bound_address
        # 0.5s timeout on client
        client = TCPClient(timeout=0.5)
        
        req = NetworkMessage(
            message_type=MessageType.HEARTBEAT,
            sender_id="alice",
            payload={}
        )
        
        with pytest.raises(socket.timeout):
            client.send_and_receive(host, port, req)
    finally:
        listener.stop()

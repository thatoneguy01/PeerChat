"""Tests for length-prefixed socket framing."""
import socket
import struct
import threading
import pytest

from peer_discovery.network.framing import (
    MAX_FRAME_SIZE,
    ConnectionClosedError,
    FramingError,
    recv_framed,
    send_framed,
)


def create_connected_sockets():
    """Create a pair of connected sockets for testing."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    
    client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    client.connect(server.getsockname())
    
    server_client, _ = server.accept()
    server.close()
    
    return client, server_client


def test_send_recv_round_trip():
    client, server = create_connected_sockets()
    try:
        data = b"hello world"
        send_framed(client, data)
        received = recv_framed(server)
        assert received == data
    finally:
        client.close()
        server.close()


def test_send_recv_large_payload():
    client, server = create_connected_sockets()
    try:
        data = b"x" * 10000
        send_framed(client, data)
        received = recv_framed(server)
        assert received == data
    finally:
        client.close()
        server.close()


def test_send_oversized_frame():
    client, server = create_connected_sockets()
    try:
        data = b"x" * (MAX_FRAME_SIZE + 1)
        with pytest.raises(FramingError, match="exceeds maximum frame size"):
            send_framed(client, data)
    finally:
        client.close()
        server.close()


def test_recv_oversized_frame():
    client, server = create_connected_sockets()
    try:
        # Manually construct an oversized header
        oversized_len = MAX_FRAME_SIZE + 1
        header = struct.pack("!I", oversized_len)
        client.sendall(header)
        
        with pytest.raises(FramingError, match="exceeds maximum"):
            recv_framed(server)
    finally:
        client.close()
        server.close()


def test_recv_connection_closed_during_header():
    client, server = create_connected_sockets()
    try:
        # Send partial header and close
        client.sendall(b"\x00\x00")
        client.close()
        
        with pytest.raises(ConnectionClosedError, match="Connection closed by peer"):
            recv_framed(server)
    finally:
        server.close()


def test_recv_connection_closed_during_payload():
    client, server = create_connected_sockets()
    try:
        # Send full header (len=10) but only partial payload
        header = struct.pack("!I", 10)
        client.sendall(header + b"12345")
        client.close()
        
        with pytest.raises(ConnectionClosedError, match="Connection closed by peer"):
            recv_framed(server)
    finally:
        server.close()

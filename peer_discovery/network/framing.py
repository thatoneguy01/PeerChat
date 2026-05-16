"""Length-prefixed framing over TCP sockets."""
import socket
import struct

# 64KB max frame size
MAX_FRAME_SIZE = 64 * 1024


class FramingError(Exception):
    """Raised for framing violations like oversized frames or incomplete reads."""
    pass


class ConnectionClosedError(Exception):
    """Raised when the remote end closes the connection cleanly."""
    pass


def send_framed(sock: socket.socket, data: bytes) -> None:
    """Send length-prefixed data over a socket.
    
    Format: 4-byte big-endian unsigned int (length) followed by the data.
    """
    length = len(data)
    if length > MAX_FRAME_SIZE:
        raise FramingError(f"Payload size {length} exceeds maximum frame size {MAX_FRAME_SIZE}")
        
    header = struct.pack("!I", length)
    try:
        sock.sendall(header + data)
    except OSError as e:
        raise ConnectionClosedError(f"Socket error during send: {e}")


def _recv_exactly(sock: socket.socket, n: int) -> bytes:
    """Receive exactly n bytes from the socket."""
    buf = bytearray()
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except socket.timeout:
            raise
        except OSError as e:
            raise ConnectionClosedError(f"Socket error during recv: {e}")
            
        if not chunk:
            raise ConnectionClosedError("Connection closed by peer")
            
        buf.extend(chunk)
    return bytes(buf)


def recv_framed(sock: socket.socket) -> bytes:
    """Receive a length-prefixed frame from a socket."""
    # Read 4-byte header
    header = _recv_exactly(sock, 4)
    length = struct.unpack("!I", header)[0]
    
    if length > MAX_FRAME_SIZE:
        raise FramingError(f"Incoming frame size {length} exceeds maximum {MAX_FRAME_SIZE}")
        
    if length == 0:
        return b""
        
    # Read payload
    return _recv_exactly(sock, length)

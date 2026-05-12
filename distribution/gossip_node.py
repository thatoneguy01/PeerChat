"""
Gossip-based message distribution node.

Each node:
  1. Listens on a TCP port for incoming gossip messages.
  2. Deduplicates messages by their UUID so each message is delivered once.
  3. On receiving a new message, delivers it locally via on_message callback,
     then forwards it to FANOUT randomly-chosen peers (epidemic gossip).

Public API
----------
    node = GossipNode(host, port, peer_registry)
    node.on_message = lambda msg: ...   # called once per unique message
    node.start()
    node.broadcast(message)             # called by UI / upper layer
    node.stop()
"""

import random
import socket
import threading
import logging
from typing import Callable, List, Optional, Set, Tuple

from .message import Message
from .peer_registry import PeerRegistry

logger = logging.getLogger(__name__)

DEFAULT_FANOUT = 3      # peers to forward to at each hop
RECV_TIMEOUT = 5        # seconds to wait for full message payload


class GossipNode:
    def __init__(
        self,
        host: str,
        port: int,
        peer_registry: PeerRegistry,
        fanout: int = DEFAULT_FANOUT,
    ) -> None:
        self.host = host
        self.port = port
        self.address = f"{host}:{port}"
        self.peer_registry = peer_registry
        self.fanout = fanout

        # Called once for every unique message this node receives or originates.
        # Set this before calling start().  Signature: (Message) -> None
        self.on_message: Optional[Callable[[Message], None]] = None

        self._seen: Set[str] = set()
        self._seen_lock = threading.Lock()
        self._running = False
        self._server_sock: Optional[socket.socket] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Open the listening socket and begin accepting gossip connections."""
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self.host, self.port))
        self._server_sock.listen(64)
        self._running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()
        logger.info("GossipNode listening on %s", self.address)

    def stop(self) -> None:
        """Shut down the listening socket."""
        self._running = False
        if self._server_sock:
            self._server_sock.close()

    def broadcast(self, message: Message) -> None:
        """
        Originate a message into the gossip network.
        Called by the UI or any upper-layer component that wants to send a chat message.
        """
        self._mark_seen(message.id)
        if self.on_message:
            self.on_message(message)
        self._forward(message)

    # ── Incoming connection handling ──────────────────────────────────────────

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, _ = self._server_sock.accept()
                threading.Thread(
                    target=self._handle_connection, args=(conn,), daemon=True
                ).start()
            except OSError:
                break

    def _handle_connection(self, conn: socket.socket) -> None:
        conn.settimeout(RECV_TIMEOUT)
        try:
            raw = _recv_framed(conn)
            if raw:
                self._receive(Message.from_json(raw))
        except Exception as exc:
            logger.debug("Connection error from peer: %s", exc)
        finally:
            conn.close()

    # ── Gossip logic ──────────────────────────────────────────────────────────

    def _receive(self, message: Message) -> None:
        """Handle a message arriving from another peer."""
        if self._is_seen(message.id):
            return                          # already processed — stop the cascade
        self._mark_seen(message.id)

        if self.on_message:
            self.on_message(message)

        if message.ttl > 0:
            message.ttl -= 1
            self._forward(message)

    def _forward(self, message: Message) -> None:
        """Pick FANOUT random peers and push the message to each."""
        candidates = self._peers_excluding(message.sender)
        targets: List[Tuple[str, int]] = random.sample(
            candidates, min(self.fanout, len(candidates))
        )
        for host, port in targets:
            _send_framed(host, port, message)

    def _peers_excluding(self, sender_addr: str) -> List[Tuple[str, int]]:
        """Return peer list minus the message's original sender and ourselves."""
        excluded = {sender_addr, self.address}
        return [
            (h, p)
            for h, p in self.peer_registry.get_peers()
            if f"{h}:{p}" not in excluded
        ]

    # ── Deduplication helpers ─────────────────────────────────────────────────

    def _is_seen(self, msg_id: str) -> bool:
        with self._seen_lock:
            return msg_id in self._seen

    def _mark_seen(self, msg_id: str) -> None:
        with self._seen_lock:
            self._seen.add(msg_id)


# ── Wire protocol helpers (length-prefixed framing) ───────────────────────────

def _send_framed(host: str, port: int, message: Message) -> None:
    """Send a single framed message to (host, port). Fire-and-forget."""
    try:
        payload = message.to_json().encode()
        with socket.create_connection((host, port), timeout=2) as sock:
            sock.sendall(len(payload).to_bytes(4, "big") + payload)
    except Exception as exc:
        logger.debug("Could not forward to %s:%d — %s", host, port, exc)


def _recv_framed(conn: socket.socket) -> Optional[str]:
    """Read one length-prefixed message from an open connection."""
    header = _recv_exactly(conn, 4)
    if header is None:
        return None
    length = int.from_bytes(header, "big")
    body = _recv_exactly(conn, length)
    return body.decode() if body else None


def _recv_exactly(conn: socket.socket, n: int) -> Optional[bytes]:
    """Read exactly n bytes, returning None if the connection closes early."""
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)

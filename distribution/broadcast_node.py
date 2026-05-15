"""
Message distribution node — WebSocket transport.

Each node:
  1. Runs a WebSocket server to receive incoming messages.
  2. Deduplicates messages by UUID so each is delivered exactly once.
  3. On a new message: fires on_message callback, then forwards to ALL peers
     with ACK + retry for currently reachable peers.

Delivery mechanism:
  - Sender forwards to every peer (not a random sample).
  - Receiver sends back {"ack": msg_id} after processing.
  - If no ACK within ACK_TIMEOUT seconds, sender retries up to MAX_RETRIES times.
  - Failed peers are logged as warnings after all retries are exhausted.

Public API
----------
    node = BroadcastNode(host, port, peer_registry)
    node.on_message = lambda msg: ...   # called once per unique message
    node.start()
    node.broadcast(message)             # called by UI / upper layer
    node.send_to_peer(host, port, message)  # direct one-peer send for recovery
    node.stop()
"""

import asyncio
import json
import threading
import logging
from dataclasses import replace
from typing import Callable, List, Optional, Set, Tuple

try:
    import websockets
except ModuleNotFoundError:
    websockets = None

from .message import Message
from .peer_registry import PeerRegistry
from .vector_clock import VectorClock, HoldBackQueue

logger = logging.getLogger(__name__)

ACK_TIMEOUT = 2.0       # seconds to wait for an ACK before retrying
MAX_RETRIES = 3         # number of delivery attempts per peer
RETRY_BACKOFF = 0.5     # seconds added per retry (0.5s, 1.0s, 1.5s)


class BroadcastNode:
    def __init__(
        self,
        host: str,
        port: int,
        peer_registry: PeerRegistry,
        fanout: int = None,     # kept for API compatibility, ignored — sends to all peers
    ) -> None:
        self.host = host
        self.port = port
        self.address = f"{host}:{port}"
        self.peer_registry = peer_registry

        # Called once per unique message this node receives or originates.
        # Set this before calling start().  Signature: (Message) -> None
        self.on_message: Optional[Callable[[Message], None]] = None

        self._seen: Set[str] = set()
        self._seen_lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None

        self._vc = VectorClock()
        self._hold_back = HoldBackQueue()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the WebSocket server in a background thread."""
        if websockets is None:
            raise RuntimeError("websockets is required; install with: pip install -r requirements.txt")
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._run_loop, daemon=True).start()
        logger.info("BroadcastNode starting on ws://%s:%d", self.host, self.port)

    def stop(self) -> None:
        """Shut down the WebSocket server."""
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    def broadcast(self, message: Message) -> None:
        """
        Send a message to all currently reachable peers.
        Called by the UI or any upper-layer component.
        """
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._do_broadcast(message), self._loop)

    def send_to_peer(self, host: str, port: int, message: Message) -> None:
        """
        Send a message to exactly one peer.

        This is intended for History/Recovery replay chunks. The message is sent
        with ttl=0 so the receiver can process it locally without re-broadcasting
        the chunk to the rest of the network.
        """
        if self._loop:
            asyncio.run_coroutine_threadsafe(
                self._send_to_peer(host, port, message), self._loop
            )

    def deduplicate(self, msg_id: str) -> bool:
        """
        Atomically check-and-mark a message ID as seen.

        Returns True  → new message, caller should process and forward it.
        Returns False → duplicate, caller should drop it.

        Thread-safe: check and mark happen inside a single lock acquisition so
        two threads racing on the same ID will never both receive True.

        Other teams can call this directly to gate their own processing:
            if node.deduplicate(msg.id):
                storage.append(msg)
        """
        with self._seen_lock:
            if msg_id in self._seen:
                return False
            self._seen.add(msg_id)
            return True

    def sync_vector_clock(self, vector_clock: dict) -> int:
        """
        Merge an externally recovered vector clock and release ready messages.

        History recovery stores messages outside the live broadcast path. If a
        node missed a live predecessor, later live messages may be sitting in
        the hold-back queue. After History backfills the missing message(s), it
        can call this with store.get_latest_vector_clock() to unblock any live
        messages that are now causally ready.

        Returns the number of held messages released.
        """
        if not isinstance(vector_clock, dict) or not vector_clock:
            return 0

        self._vc.merge(vector_clock)
        released = self._hold_back.drain(self._vc)
        for msg in released:
            if self.on_message:
                self.on_message(msg)
        return len(released)

    def debug_state(self) -> dict:
        """Return a small snapshot useful during integration testing."""
        return {
            "address": self.address,
            "vector_clock": self._vc.snapshot(),
            "seen_count": len(self._seen),
            "hold_back_count": len(self._hold_back),
            "hold_back": self._hold_back.snapshot(),
        }

    # ── Event loop ────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        if websockets is None:
            raise RuntimeError("websockets is required; install with: pip install -r requirements.txt")
        self._stop_event = asyncio.Event()
        async with websockets.serve(self._handle_ws, self.host, self.port):
            logger.info("BroadcastNode listening on ws://%s:%d", self.host, self.port)
            await self._stop_event.wait()

    # ── Incoming message handling ─────────────────────────────────────────────

    async def _handle_ws(self, websocket) -> None:
        """Receive a message, process it, and send an ACK back to the sender."""
        try:
            async for raw in websocket:
                message = Message.from_json(raw)
                await self._receive(message)
                await websocket.send(json.dumps({"ack": message.id}))
        except Exception as exc:
            if websockets is not None and isinstance(exc, websockets.ConnectionClosed):
                return
            logger.debug("Error handling peer connection: %s", exc)

    async def _receive(self, message: Message) -> bool:
        """Process a message arriving from another peer.

        Returns True when this node processed the message for the first time.
        Returns False for duplicates.
        """
        if not self.deduplicate(message.id):
            return False                        # already seen — stop the cascade

        if self._vc.is_ready(message):
            self._vc.merge(message.vector_clock)
            to_deliver = [message] + self._hold_back.drain(self._vc)
        else:
            self._hold_back.add(message)
            to_deliver = []

        for msg in to_deliver:
            if self.on_message:
                self.on_message(msg)

        if message.ttl > 0:
            await self._forward(replace(message, ttl=message.ttl - 1))

        return True

    # ── Broadcast logic ───────────────────────────────────────────────────────

    async def _do_broadcast(self, message: Message) -> bool:
        """Originate a broadcast from this node."""
        if not self.deduplicate(message.id):
            return False
        self._vc.increment(self.address)
        message.vector_clock = self._vc.snapshot()
        if self.on_message:
            self.on_message(message)
        if message.ttl > 0:
            await self._forward(message)
        return True

    async def _send_to_peer(self, host: str, port: int, message: Message) -> None:
        """Send to one peer without fanout."""
        direct_message = replace(message, ttl=0)
        await self._send_with_retry(host, port, direct_message)

    async def _forward(self, message: Message) -> None:
        """Send to ALL peers concurrently, each with ACK + retry."""
        targets = self._peers_excluding(message.sender)
        await asyncio.gather(*[self._send_with_retry(h, p, message) for h, p in targets])

    async def _send_with_retry(self, host: str, port: int, message: Message) -> None:
        """
        Deliver one message to one peer.
        Retries up to MAX_RETRIES times if no ACK is received within ACK_TIMEOUT.
        """
        uri = f"ws://{host}:{port}"
        if websockets is None:
            raise RuntimeError("websockets is required; install with: pip install -r requirements.txt")
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                async with websockets.connect(uri, open_timeout=2, close_timeout=2) as ws:
                    await ws.send(message.to_json())
                    ack_raw = await asyncio.wait_for(ws.recv(), timeout=ACK_TIMEOUT)
                    ack = json.loads(ack_raw)
                    if ack.get("ack") == message.id:
                        logger.debug("ACK received from %s:%d", host, port)
                        return                  # delivery confirmed
            except Exception as exc:
                logger.debug(
                    "Attempt %d/%d to %s:%d failed — %s", attempt, MAX_RETRIES, host, port, exc
                )
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BACKOFF * attempt)

        logger.warning("Could not deliver message %s to %s:%d after %d attempts",
                       message.id[:8], host, port, MAX_RETRIES)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _peers_excluding(self, sender_addr: str) -> List[Tuple[str, int]]:
        excluded = {sender_addr, self.address}
        return [
            (h, p)
            for h, p in self.peer_registry.get_peers()
            if f"{h}:{p}" not in excluded
        ]

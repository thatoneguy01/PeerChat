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
from collections import deque
from dataclasses import replace
from concurrent.futures import Future
from typing import Callable, Deque, List, Optional, Set, Tuple

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
READY_TIMEOUT = 2.0     # seconds to wait for a reconnect ready probe
PENDING_QUEUE_LIMIT = 100


class BroadcastNode:
    def __init__(
        self,
        host: str,
        port: int,
        peer_registry: PeerRegistry,
        fanout: int = None,     # kept for API compatibility, ignored — sends to all peers
        pending_queue_limit: int = PENDING_QUEUE_LIMIT,
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
        self._thread: Optional[threading.Thread] = None
        self._server_ready: Optional[threading.Event] = None
        self._startup_error: Optional[BaseException] = None

        self._vc = VectorClock()
        self._hold_back = HoldBackQueue()
        self._pending: dict[Tuple[str, int], Deque[Message]] = {}
        self._pending_lock = threading.Lock()
        self.pending_queue_limit = pending_queue_limit

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the WebSocket server in a background thread."""
        if websockets is None:
            raise RuntimeError("websockets is required; install with: pip install -r requirements.txt")
        if self._thread and self._thread.is_alive():
            raise RuntimeError("BroadcastNode is already running")

        self._startup_error = None
        self._server_ready = threading.Event()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        if not self._server_ready.wait(timeout=READY_TIMEOUT + 1.0):
            raise RuntimeError(f"Timed out starting BroadcastNode on {self.address}")
        if self._startup_error:
            raise RuntimeError(f"Could not start BroadcastNode on {self.address}") from self._startup_error

        logger.info("BroadcastNode starting on ws://%s:%d", self.host, self.port)

    def stop(self) -> None:
        """Shut down the WebSocket server."""
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread and threading.current_thread() is not self._thread:
            self._thread.join(timeout=READY_TIMEOUT + 1.0)
            if self._thread.is_alive():
                logger.warning("BroadcastNode on %s did not stop cleanly", self.address)
                return
        self._thread = None
        self._loop = None
        self._stop_event = None
        self._server_ready = None

    def broadcast(self, message: Message) -> None:
        """
        Send a message to all currently reachable peers.
        Called by the UI or any upper-layer component.
        """
        if self._loop:
            future = asyncio.run_coroutine_threadsafe(self._do_broadcast(message), self._loop)
            if threading.current_thread() is not self._thread:
                try:
                    future.result(timeout=self._delivery_deadline())
                except Exception as exc:
                    logger.debug("Broadcast did not finish before timeout: %s", exc)

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

    def check_peer_ready(self, host: str, port: int, timeout: float = READY_TIMEOUT) -> bool:
        """
        Verify that a peer can receive and respond on its WebSocket port.

        This is a lightweight two-way reconnect check. It catches the common
        case where discovery thinks a peer is back, but its WebSocket server is
        not actually ready yet.
        """
        if not self._loop:
            return False
        future = asyncio.run_coroutine_threadsafe(
            self._probe_peer(host, port, timeout=timeout), self._loop
        )
        try:
            return bool(future.result(timeout=timeout + 1.0))
        except Exception:
            return False

    def retry_pending(self, host: str, port: int) -> Optional[Future]:
        """
        Flush queued sends for a peer after Peer Discovery reports reconnect.

        Returns a Future resolving to the number of messages delivered, or None
        if this node is not running.
        """
        if not self._loop:
            return None
        return asyncio.run_coroutine_threadsafe(
            self._flush_pending_for_peer(host, port), self._loop
        )

    def pending_count(self, host: str = None, port: int = None) -> int:
        """Return queued message count, optionally for one peer."""
        with self._pending_lock:
            if host is not None and port is not None:
                return len(self._pending.get((host, port), ()))
            return sum(len(q) for q in self._pending.values())

    def _delivery_deadline(self) -> float:
        return (MAX_RETRIES * ACK_TIMEOUT) + sum(
            RETRY_BACKOFF * attempt for attempt in range(1, MAX_RETRIES)
        ) + 1.0

    def sync_vector_clock(self, vc: dict) -> None:
        """
        Advance the local vector clock to at least the values in vc, then drain
        any hold-back queue entries that are now causally ready.

        Call this after history recovery completes so the causal layer is not
        blocked by messages that were replayed through the recovery path rather
        than received via live broadcast.

        Thread-safe: schedules the update on the asyncio event loop.
        """
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._apply_vc_sync(vc), self._loop)

    async def _apply_vc_sync(self, vc: dict) -> None:
        self._vc.merge(vc)
        for msg in self._hold_back.drain(self._vc):
            if self.on_message:
                self.on_message(msg)

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

    # ── Event loop ────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except BaseException as exc:
            self._startup_error = exc
            if self._server_ready:
                self._server_ready.set()
            logger.debug("BroadcastNode loop stopped with error: %s", exc)
        finally:
            if self._loop:
                self._loop.close()

    async def _serve(self) -> None:
        if websockets is None:
            raise RuntimeError("websockets is required; install with: pip install -r requirements.txt")
        self._stop_event = asyncio.Event()
        async with websockets.serve(self._handle_ws, self.host, self.port):
            logger.info("BroadcastNode listening on ws://%s:%d", self.host, self.port)
            if self._server_ready:
                self._server_ready.set()
            await self._stop_event.wait()

    # ── Incoming message handling ─────────────────────────────────────────────

    async def _handle_ws(self, websocket) -> None:
        """Receive a message, process it, and send an ACK back to the sender."""
        try:
            async for raw in websocket:
                if await self._handle_control_message(websocket, raw):
                    continue
                message = Message.from_json(raw)
                processed = await self._deliver_incoming(message)
                await websocket.send(json.dumps({"ack": message.id}))
                if processed and message.ttl > 0:
                    await self._forward(replace(message, ttl=message.ttl - 1))
        except Exception as exc:
            if websockets is not None and isinstance(exc, websockets.ConnectionClosed):
                return
            logger.debug("Error handling peer connection: %s", exc)

    async def _handle_control_message(self, websocket, raw: str) -> bool:
        """Handle transport-level messages that are not chat messages."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return False

        if data.get("type") == "ready_probe":
            await websocket.send(json.dumps({
                "type": "ready_ack",
                "from": self.address,
            }))
            return True
        return False

    async def _receive(self, message: Message) -> bool:
        """Process a message arriving from another peer.

        Returns True when this node processed the message for the first time.
        Returns False for duplicates.
        """
        processed = await self._deliver_incoming(message)
        if processed and message.ttl > 0:
            await self._forward(replace(message, ttl=message.ttl - 1))
        return processed

    async def _deliver_incoming(self, message: Message) -> bool:
        """Deduplicate and deliver/hold a message without forwarding it."""
        if not self.deduplicate(message.id):
            return False                        # already seen — stop the cascade

        if self._vc.is_ready(message):
            self._vc.merge(message.vector_clock)
            to_deliver = [message] + self._hold_back.drain(self._vc)
        else:
            self._hold_back.add(message)
            to_deliver = self._hold_back.drain(self._vc)

        for msg in to_deliver:
            if self.on_message:
                self.on_message(msg)

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

    async def _send_with_retry(
        self,
        host: str,
        port: int,
        message: Message,
        *,
        queue_on_failure: bool = True,
        flush_on_success: bool = True,
    ) -> bool:
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
                        if flush_on_success:
                            await self._flush_pending_for_peer(host, port)
                        return True             # delivery confirmed
            except Exception as exc:
                logger.debug(
                    "Attempt %d/%d to %s:%d failed — %s", attempt, MAX_RETRIES, host, port, exc
                )
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BACKOFF * attempt)

        logger.warning("Could not deliver message %s to %s:%d after %d attempts",
                       message.id[:8], host, port, MAX_RETRIES)
        if queue_on_failure:
            self._queue_pending(host, port, message)
        return False

    async def _probe_peer(self, host: str, port: int, timeout: float = READY_TIMEOUT) -> bool:
        """Return True only when a peer accepts a ready probe and responds."""
        if websockets is None:
            raise RuntimeError("websockets is required; install with: pip install -r requirements.txt")
        uri = f"ws://{host}:{port}"
        try:
            async with websockets.connect(uri, open_timeout=timeout, close_timeout=timeout) as ws:
                await ws.send(json.dumps({
                    "type": "ready_probe",
                    "from": self.address,
                }))
                raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                ack = json.loads(raw)
                return ack.get("type") == "ready_ack"
        except Exception as exc:
            logger.debug("Ready probe to %s:%d failed — %s", host, port, exc)
            return False

    def _queue_pending(self, host: str, port: int, message: Message) -> None:
        """Keep a bounded retry queue for peers that are temporarily down."""
        key = (host, port)
        with self._pending_lock:
            if key not in self._pending:
                self._pending[key] = deque(maxlen=self.pending_queue_limit)
            self._pending[key].append(message)

    def _take_pending(self, host: str, port: int) -> List[Message]:
        key = (host, port)
        with self._pending_lock:
            q = self._pending.get(key)
            if not q:
                return []
            messages = list(q)
            q.clear()
            return messages

    def _requeue_pending_front(self, host: str, port: int, messages: List[Message]) -> None:
        if not messages:
            return
        key = (host, port)
        with self._pending_lock:
            old = list(self._pending.get(key, ()))
            q = deque(maxlen=self.pending_queue_limit)
            for msg in messages + old:
                q.append(msg)
            self._pending[key] = q

    async def _flush_pending_for_peer(self, host: str, port: int) -> int:
        """Try to deliver queued messages after a peer is reachable again."""
        messages = self._take_pending(host, port)
        if not messages:
            return 0

        if not await self._probe_peer(host, port):
            self._requeue_pending_front(host, port, messages)
            return 0

        delivered = 0
        failed: List[Message] = []
        for idx, msg in enumerate(messages):
            ok = await self._send_with_retry(
                host,
                port,
                msg,
                queue_on_failure=False,
                flush_on_success=False,
            )
            if ok:
                delivered += 1
            else:
                failed.extend(messages[idx:])
                break

        self._requeue_pending_front(host, port, failed)
        return delivered

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _peers_excluding(self, sender_addr: str) -> List[Tuple[str, int]]:
        excluded = {sender_addr, self.address}
        return [
            (h, p)
            for h, p in self.peer_registry.get_peers()
            if f"{h}:{p}" not in excluded
        ]

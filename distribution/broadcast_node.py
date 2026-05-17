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
from typing import Callable, Dict, List, Optional, Set, Tuple

try:
    import websockets
except ModuleNotFoundError:
    websockets = None

from .message import Message
from .peer_registry import PeerRegistry
from .vector_clock import VectorClock, HoldBackQueue

try:
    from security import sign as _sign, verify as _verify, register_public_key as _register_pub_key
    from security.payload_encryption import encrypt_payload as _encrypt_payload
    from security.payload_encryption import is_encrypted_content as _is_encrypted_content
except ImportError:
    _sign = _verify = _register_pub_key = None
    _encrypt_payload = _is_encrypted_content = None

logger = logging.getLogger(__name__)

ACK_TIMEOUT = 2.0           # seconds to wait for an ACK before retrying
MAX_RETRIES = 3             # number of delivery attempts per peer
RETRY_BACKOFF = 0.5         # seconds added per retry (0.5s, 1.0s, 1.5s)
RETRY_FLUSH_INTERVAL = 10.0 # seconds between retry queue flush attempts


class BroadcastNode:
    def __init__(
        self,
        host: str,
        port: int,
        peer_registry: PeerRegistry,
        fanout: int = None,     # kept for API compatibility, ignored — sends to all peers
        enforce_signatures: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.address = f"{host}:{port}"
        self.peer_registry = peer_registry
        self.enforce_signatures = enforce_signatures
        self.own_public_key_pem: bytes | None = None

        # Called once per unique message this node receives or originates.
        # Set this before calling start().  Signature: (Message) -> None
        self.on_message: Optional[Callable[[Message], None]] = None

        self._seen: Set[str] = set()
        self._seen_lock = threading.Lock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._thread: Optional[threading.Thread] = None

        self._retry_queue: Dict[str, List[Message]] = {}
        self._retry_lock = threading.Lock()

        self._vc = VectorClock()
        self._hold_back = HoldBackQueue()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the WebSocket server in a background thread."""
        if websockets is None:
            raise RuntimeError("websockets is required; install with: pip install -r requirements.txt")
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("BroadcastNode starting on ws://%s:%d", self.host, self.port)

    def stop(self) -> None:
        """Shut down the WebSocket server and wait for the port to be fully released."""
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread:
            self._thread.join(timeout=5.0)
        self._loop = None
        self._stop_event = None
        self._thread = None

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
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        if websockets is None:
            raise RuntimeError("websockets is required; install with: pip install -r requirements.txt")
        self._stop_event = asyncio.Event()
        async with websockets.serve(self._handle_ws, self.host, self.port):
            logger.info("BroadcastNode listening on ws://%s:%d", self.host, self.port)
            retry_task = asyncio.ensure_future(self._retry_background_task())
            hello_task = asyncio.ensure_future(self._send_hello_to_all_peers())
            await self._stop_event.wait()
            retry_task.cancel()
            hello_task.cancel()
            await asyncio.gather(retry_task, hello_task, return_exceptions=True)

    # ── Incoming message handling ─────────────────────────────────────────────

    async def _handle_ws(self, websocket) -> None:
        """Route incoming frames: handshake messages or chat messages."""
        try:
            async for raw in websocket:
                data = json.loads(raw)
                msg_type = data.get("type")
                if msg_type == "hello":
                    await websocket.send(json.dumps({"type": "hello_ack", "sender": self.address}))
                    logger.debug("hello from %s — sent hello_ack", data.get("sender"))
                    continue
                if msg_type == "hello_ack":
                    logger.info("Two-way link confirmed with %s", data.get("sender"))
                    continue
                message = Message.from_json(raw)
                if not self._verify_incoming(message):
                    await websocket.send(json.dumps({
                        "nack": message.id,
                        "reason": "signature_failed_or_missing_key",
                    }))
                    continue
                await websocket.send(json.dumps({"ack": message.id}))
                asyncio.ensure_future(self._receive(message))
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
            to_deliver = self._hold_back.drain(self._vc)

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
        if not self._encrypt_outgoing(message):
            return False
        if not self._sign_outgoing(message):
            return False
        if self.on_message:
            self.on_message(message)
        if message.ttl > 0:
            await self._forward(message)
        return True

    async def _send_to_peer(self, host: str, port: int, message: Message) -> None:
        """Send to one peer without fanout."""
        direct_message = replace(message, ttl=0)
        if not self._encrypt_outgoing(direct_message):
            return
        if not self._sign_outgoing(direct_message):
            return
        await self._send_with_retry(host, port, direct_message)

    async def _forward(self, message: Message) -> None:
        """Send to ALL peers concurrently, each with ACK + retry."""
        targets = self._peers_excluding(message.sender)
        await asyncio.gather(*[self._send_with_retry(h, p, message) for h, p in targets])

    async def _send_with_retry(
        self, host: str, port: int, message: Message, *, queue_on_fail: bool = True
    ) -> bool:
        """
        Deliver one message to one peer. Returns True on success.
        On total failure, queues the message for later retry if queue_on_fail is True.
        Pass queue_on_fail=False when flushing the retry queue to avoid re-queuing.
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
                        return True
            except Exception as exc:
                logger.debug(
                    "Attempt %d/%d to %s:%d failed — %s", attempt, MAX_RETRIES, host, port, exc
                )
            if attempt < MAX_RETRIES:
                await asyncio.sleep(RETRY_BACKOFF * attempt)

        if queue_on_fail:
            peer_key = f"{host}:{port}"
            with self._retry_lock:
                self._retry_queue.setdefault(peer_key, []).append(message)
            logger.warning(
                "Queued message %s for %s:%d — peer unreachable, will retry when back online",
                message.id[:8], host, port,
            )
        else:
            logger.warning(
                "Could not deliver message %s to %s:%d after %d attempts",
                message.id[:8], host, port, MAX_RETRIES,
            )
        return False

    # ── Reconnect / handshake / retry queue ──────────────────────────────────

    async def _send_hello_to_all_peers(self) -> None:
        """Send hello to every known peer after startup to confirm two-way connectivity."""
        await asyncio.sleep(0.5)  # let the server finish binding first
        for host, port in self.peer_registry.get_peers():
            self._register_peer_key(host, port)
            asyncio.ensure_future(self._do_handshake(host, port))

    def _register_peer_key(self, host: str, port: int) -> None:
        """Register a peer's public key with the security module if available."""
        if _register_pub_key is None:
            return
        if not hasattr(self.peer_registry, "get_pub_key"):
            return
        pub_key = self.peer_registry.get_pub_key(host, port)
        if pub_key:
            try:
                if isinstance(pub_key, bytes):
                    pub_key_bytes = pub_key
                else:
                    pub_key_bytes = str(pub_key).encode()
                _register_pub_key(f"{host}:{port}", pub_key_bytes)
            except Exception as exc:
                logger.warning("could not register pub key for %s:%d — %s", host, port, exc)

    async def _do_handshake(self, host: str, port: int) -> None:
        """
        Open a short-lived connection to one peer, exchange hello/hello_ack, and
        confirm both directions are working.  On success, flush any messages queued
        for that peer while it was offline.
        """
        uri = f"ws://{host}:{port}"
        try:
            async with websockets.connect(uri, open_timeout=2, close_timeout=2) as ws:
                await ws.send(json.dumps({"type": "hello", "sender": self.address}))
                raw = await asyncio.wait_for(ws.recv(), timeout=ACK_TIMEOUT)
                data = json.loads(raw)
                if data.get("type") == "hello_ack":
                    logger.info("Two-way link confirmed with %s:%d", host, port)
                    asyncio.ensure_future(self._flush_peer_retry_queue(host, port))
                else:
                    logger.warning(
                        "Unexpected handshake response from %s:%d: %s", host, port, data
                    )
        except Exception as exc:
            logger.warning("Handshake with %s:%d failed — %s", host, port, exc)

    async def _retry_background_task(self) -> None:
        """Every RETRY_FLUSH_INTERVAL seconds, attempt to flush queued messages for offline peers."""
        while not self._stop_event.is_set():
            await asyncio.sleep(RETRY_FLUSH_INTERVAL)
            with self._retry_lock:
                pending = [k for k, v in self._retry_queue.items() if v]
            for peer_key in pending:
                host, port_str = peer_key.rsplit(":", 1)
                asyncio.ensure_future(self._flush_peer_retry_queue(host, int(port_str)))

    async def _flush_peer_retry_queue(self, host: str, port: int) -> None:
        """
        Try to deliver all messages queued for one peer.
        Clears the queue optimistically; re-queues anything that still fails.
        """
        peer_key = f"{host}:{port}"
        with self._retry_lock:
            messages = list(self._retry_queue.pop(peer_key, []))
        if not messages:
            return
        flushed = 0
        for i, msg in enumerate(messages):
            success = await self._send_with_retry(host, port, msg, queue_on_fail=False)
            if success:
                flushed += 1
            else:
                still_failed = messages[i:]
                with self._retry_lock:
                    self._retry_queue[peer_key] = (
                        still_failed + self._retry_queue.get(peer_key, [])
                    )
                break
        if flushed:
            logger.info("Flushed %d queued messages to %s:%d", flushed, host, port)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _peers_excluding(self, sender_addr: str) -> List[Tuple[str, int]]:
        excluded = {sender_addr, self.address}
        return [
            (h, p)
            for h, p in self.peer_registry.get_peers()
            if f"{h}:{p}" not in excluded
        ]

    def _verify_incoming(self, message: Message) -> bool:
        """Check message integrity before ACK, dedup, delivery, or forwarding."""
        if _verify is None:
            return not self.enforce_signatures

        signature_required = self.enforce_signatures or self._has_any_peer_key()
        if not signature_required:
            return True

        if not message.signature:
            logger.warning(
                "dropping message %s from %s — missing signature",
                message.id[:8], message.sender,
            )
            return False

        sender_parts = message.sender.rsplit(":", 1)
        if len(sender_parts) != 2:
            logger.warning(
                "dropping message %s — invalid sender address %s",
                message.id[:8], message.sender,
            )
            return False

        host, port_str = sender_parts
        try:
            port = int(port_str)
        except ValueError:
            logger.warning(
                "dropping message %s — invalid sender port %s",
                message.id[:8], message.sender,
            )
            return False

        if not hasattr(self.peer_registry, "get_pub_key"):
            logger.warning(
                "dropping message %s from %s — no peer key registry",
                message.id[:8], message.sender,
            )
            return False

        if not self.peer_registry.get_pub_key(host, port):
            logger.warning(
                "dropping message %s from %s — missing sender public key",
                message.id[:8], message.sender,
            )
            return False

        self._register_peer_key(host, port)
        if not _verify(message):
            logger.warning(
                "dropping message %s from %s — signature verification failed",
                message.id[:8], message.sender,
            )
            return False

        return True

    def _encrypt_outgoing(self, message: Message) -> bool:
        """Encrypt payload before signing when originating from this node."""
        if _encrypt_payload is None or _is_encrypted_content is None:
            return True
        if _is_encrypted_content(message.content):
            return True

        pubkeys = self._gather_recipient_pubkeys()
        if not pubkeys:
            return True

        try:
            _encrypt_payload(message, pubkeys, own_user_id=self.address)
            return True
        except Exception:
            logger.warning(
                "payload encryption failed for message %s; sending plaintext",
                message.id[:8],
                exc_info=True,
            )
            return True

    def _gather_recipient_pubkeys(self) -> dict[str, bytes]:
        pubkeys: dict[str, bytes] = {}
        if self.own_public_key_pem:
            pubkeys[self.address] = self.own_public_key_pem
        if hasattr(self.peer_registry, "get_pub_key"):
            for host, port in self.peer_registry.get_peers():
                user_id = f"{host}:{port}"
                pub = self.peer_registry.get_pub_key(host, port)
                if pub:
                    pubkeys[user_id] = pub.encode("utf-8") if isinstance(pub, str) else pub
        return pubkeys

    def _sign_outgoing(self, message: Message) -> bool:
        if _sign is None:
            return not self.enforce_signatures
        try:
            _sign(message)
            return True
        except RuntimeError:
            if self.enforce_signatures:
                logger.warning(
                    "dropping outgoing message %s — private key not configured",
                    message.id[:8],
                )
                return False
            logger.debug("sign skipped: private key not configured")
            return True

    def _has_any_peer_key(self) -> bool:
        if not hasattr(self.peer_registry, "get_pub_key"):
            return False
        try:
            return any(
                bool(self.peer_registry.get_pub_key(host, port))
                for host, port in self.peer_registry.get_peers()
            )
        except Exception:
            return False

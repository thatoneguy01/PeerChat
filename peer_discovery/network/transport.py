"""WebSocket transport layer.

Replaces the previous raw TCP + length-prefixed JSON transport. Uses the
``websockets`` library's synchronous API (``websockets.sync``) so we keep
the existing threading model — no asyncio refactor required.

WebSocket handles framing natively (one TEXT frame = one message), so the
old ``framing.py`` module is gone. The public API of this module
(WebSocketListener / WebSocketClient + MessageHandler signature) intentionally
mirrors the old TCPListener / TCPClient so other modules in this package
need only minimal changes.
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from websockets.exceptions import ConnectionClosed, InvalidMessage
from websockets.sync.client import connect as ws_connect
from websockets.sync.server import ServerConnection, serve

from .protocol import NetworkMessage, ProtocolError, decode_message, encode_message

logger = logging.getLogger(__name__)

# Callback signature: (source_ip, message) -> Optional[response_message]
MessageHandler = Callable[[str, NetworkMessage], NetworkMessage | None]


class WebSocketListener:
    """A WebSocket server that accepts one-message connections and dispatches
    incoming messages to a handler. Each connection is short-lived: the server
    reads one TEXT frame, calls the handler, optionally sends one reply, and
    closes. This matches the prior raw-TCP semantics exactly.
    """

    def __init__(self, host: str, port: int, handler: MessageHandler, max_workers: int = 20):
        self.host = host
        self.port = port
        self.handler = handler
        self.max_workers = max_workers

        # We let websockets.sync.serve manage the accept loop on its own thread.
        # Per-connection handling still happens on its internal worker threads.
        # We expose our own ThreadPoolExecutor for outbound fire-and-forget
        # sends (gossip / heartbeat fan-out) — preserved for compatibility
        # with the existing call sites that submit to `listener._executor`.
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="ws-worker",
        )
        self._server = None  # set in start()
        self._server_thread: threading.Thread | None = None
        self._running = False
        # Will be set in start() once the server actually binds.
        self.bound_address: tuple[str, int] | None = None

    def start(self) -> None:
        # Create the server. websockets.sync.server.serve() binds immediately
        # and returns a Server object. serve_forever() blocks, so we run it
        # in a dedicated thread.
        self._server = serve(self._handle_ws_client, self.host, self.port)
        # The websockets Server exposes the underlying socket on `.socket`.
        sock_name = self._server.socket.getsockname()
        self.bound_address = (sock_name[0], sock_name[1])
        self._running = True

        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="ws-accept",
        )
        self._server_thread.start()
        logger.info(
            "listener_listening bind=%s:%d workers=%d",
            self.host, self.bound_address[1], self.max_workers,
        )

    def stop(self) -> None:
        port = self.bound_address[1] if self.bound_address else self.port
        logger.info("listener_stopping bind=%s:%d", self.host, port)
        self._running = False
        if self._server is not None:
            try:
                self._server.shutdown()
            except Exception:
                pass
        self._executor.shutdown(wait=False, cancel_futures=True)
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=1.0)
        logger.info("listener_stopped bind=%s:%d", self.host, port)

    def _handle_ws_client(self, ws: ServerConnection) -> None:
        # The remote address. websockets exposes it on ws.remote_address as
        # (host, port). On servers behind proxies you'd inspect headers, but
        # for a LAN demo the socket address is correct.
        try:
            ip = ws.remote_address[0]
        except Exception:
            ip = "<unknown>"

        try:
            try:
                raw = ws.recv(timeout=30.0)
            except TimeoutError:
                logger.warning("conn_timeout from=%s (30s)", ip)
                return
            except ConnectionClosed:
                logger.debug("conn_closed from=%s before_any_data", ip)
                return

            if raw is None or raw == "" or raw == b"":
                logger.debug("conn_closed from=%s before_any_data", ip)
                return

            msg = decode_message(raw)
            payload_len = len(raw) if isinstance(raw, (bytes, str)) else 0
            logger.debug(
                "conn_decoded from=%s type=%s sender=%s bytes=%d",
                ip, msg.message_type.value, msg.sender_id, payload_len,
            )
            response = self.handler(ip, msg)

            if response:
                encoded = encode_message(response)
                ws.send(encoded)
                logger.debug(
                    "conn_replied to=%s type=%s bytes=%d",
                    ip, response.message_type.value, len(encoded),
                )
        except ProtocolError as e:
            # WebSocket layer prevents non-WS traffic from reaching us at all
            # (handshake rejects HTTP and bad protocols upstream). What's left
            # here is malformed JSON inside an otherwise valid WS frame.
            logger.warning("protocol_error from=%s err=%s", ip, e)
        except InvalidMessage as e:
            # A non-WS client tried to connect (e.g., a plain HTTP request).
            # websockets rejects it during handshake before reaching us, but
            # log defensively in case the exception surfaces here.
            logger.warning("invalid_ws_handshake from=%s err=%s", ip, e)
        except ConnectionClosed:
            logger.debug("conn_closed_normally from=%s", ip)
        except Exception as e:
            logger.error("conn_unexpected_error from=%s err=%s", ip, e, exc_info=True)


class WebSocketClient:
    """A client for sending a single message and optionally receiving a
    response. Opens a short-lived WebSocket connection per call — same pattern
    as the prior TCPClient.
    """

    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def send_and_receive(self, host: str, port: int, msg: NetworkMessage) -> NetworkMessage | None:
        """Send a message and wait for an optional response.

        Connect-level failures (refused, timed out, bad handshake) propagate
        to the caller — bootstrap.py catches them and produces a specific
        diagnostic ("bootstrap_send_failed err=...") so the user can tell
        firewall / AP isolation issues from in-band reply failures.

        Returns ``None`` only when the connection succeeded but the peer
        closed without sending a reply, or the reply was empty.
        """
        uri = f"ws://{host}:{port}/"
        logger.info(
            "client_connect to=%s:%d type=%s timeout=%.1fs",
            host, port, msg.message_type.value, self.timeout,
        )
        with ws_connect(
            uri,
            open_timeout=self.timeout,
            close_timeout=1.0,
            max_size=2 ** 22,  # 4 MiB, generous for snapshot deliveries
        ) as ws:
            logger.debug("client_connected to=%s:%d", host, port)

            encoded = encode_message(msg)
            ws.send(encoded)
            logger.info(
                "client_sent to=%s:%d type=%s bytes=%d",
                host, port, msg.message_type.value, len(encoded),
            )

            try:
                raw = ws.recv(timeout=self.timeout)
            except TimeoutError:
                logger.warning(
                    "client_recv_timeout from=%s:%d after=%.1fs",
                    host, port, self.timeout,
                )
                return None
            except ConnectionClosed:
                logger.warning("client_recv_conn_closed from=%s:%d", host, port)
                return None

            if not raw:
                logger.warning(
                    "client_recv_empty from=%s:%d — peer closed without reply",
                    host, port,
                )
                return None

            reply = decode_message(raw)
            payload_len = len(raw) if isinstance(raw, (bytes, str)) else 0
            logger.info(
                "client_recv from=%s:%d type=%s bytes=%d",
                host, port, reply.message_type.value, payload_len,
            )
            return reply

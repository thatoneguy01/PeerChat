"""TCP Transport Layer."""
import logging
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from .framing import ConnectionClosedError, FramingError, recv_framed, send_framed
from .protocol import NetworkMessage, ProtocolError, decode_message, encode_message

logger = logging.getLogger(__name__)

# Callback signature: (source_ip, message) -> Optional[response_message]
MessageHandler = Callable[[str, NetworkMessage], NetworkMessage | None]


class TCPListener:
    """A threaded TCP listener that accepts connections and dispatches to a handler."""
    
    def __init__(self, host: str, port: int, handler: MessageHandler, max_workers: int = 20):
        self.host = host
        self.port = port
        self.handler = handler
        self.max_workers = max_workers
        
        self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind((host, port))
        
        # In case port 0 was provided, save the actual bound address
        self.bound_address = self._server.getsockname()
        
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="tcp-worker")
        self._running = False
        self._accept_thread: threading.Thread | None = None

    def start(self) -> None:
        self._server.listen()
        self._running = True
        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True, name="tcp-accept")
        self._accept_thread.start()
        logger.info(
            "listener_listening bind=%s:%d workers=%d",
            self.host, self.bound_address[1], self.max_workers,
        )

    def stop(self) -> None:
        logger.info("listener_stopping bind=%s:%d", self.host, self.bound_address[1])
        self._running = False
        try:
            self._server.close()
        except OSError:
            pass
        self._executor.shutdown(wait=False, cancel_futures=True)
        if self._accept_thread and self._accept_thread.is_alive():
            self._accept_thread.join(timeout=1.0)
        logger.info("listener_stopped bind=%s:%d", self.host, self.bound_address[1])

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, addr = self._server.accept()
                conn.settimeout(30.0)
                logger.debug("accept from=%s:%d", addr[0], addr[1])
                self._executor.submit(self._handle_client, conn, addr[0])
            except OSError:
                if not self._running:
                    break

    def _handle_client(self, conn: socket.socket, ip: str) -> None:
        try:
            with conn:
                raw = recv_framed(conn)
                if not raw:
                    logger.debug("conn_closed from=%s before_any_data", ip)
                    return

                msg = decode_message(raw)
                logger.debug(
                    "conn_decoded from=%s type=%s sender=%s bytes=%d",
                    ip, msg.message_type.value, msg.sender_id, len(raw),
                )
                response = self.handler(ip, msg)

                if response:
                    encoded = encode_message(response)
                    send_framed(conn, encoded)
                    logger.debug(
                        "conn_replied to=%s type=%s bytes=%d",
                        ip, response.message_type.value, len(encoded),
                    )
        except (FramingError, ProtocolError) as e:
            # Most common cause: another protocol (HTTP, WebSocket, etc.)
            # hit our discovery port by mistake. Make that diagnosis obvious.
            err_str = str(e)
            if "frame size" in err_str.lower() and "exceeds maximum" in err_str.lower():
                logger.warning(
                    "protocol_error from=%s err=%s — looks like non-discovery "
                    "traffic (HTTP/WebSocket?) hit our TCP port; check that "
                    "other components are using the right port",
                    ip, err_str,
                )
            else:
                logger.warning("protocol_error from=%s err=%s", ip, err_str)
        except socket.timeout:
            logger.warning("conn_timeout from=%s (30s)", ip)
        except ConnectionClosedError:
            logger.debug("conn_closed_normally from=%s", ip)
        except Exception as e:
            logger.error("conn_unexpected_error from=%s err=%s", ip, e, exc_info=True)


class TCPClient:
    """A client for sending single messages and optionally receiving a response."""
    
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def send_and_receive(self, host: str, port: int, msg: NetworkMessage) -> NetworkMessage | None:
        """Send a message and wait for an optional response."""
        logger.info(
            "client_connect to=%s:%d type=%s timeout=%.1fs",
            host, port, msg.message_type.value, self.timeout,
        )
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout)
            sock.connect((host, port))
            logger.debug("client_connected to=%s:%d", host, port)

            encoded = encode_message(msg)
            send_framed(sock, encoded)
            logger.info(
                "client_sent to=%s:%d type=%s bytes=%d",
                host, port, msg.message_type.value, len(encoded),
            )

            try:
                raw = recv_framed(sock)
                if raw:
                    reply = decode_message(raw)
                    logger.info(
                        "client_recv from=%s:%d type=%s bytes=%d",
                        host, port, reply.message_type.value, len(raw),
                    )
                    return reply
                logger.warning("client_recv_empty from=%s:%d — peer closed without reply", host, port)
            except ConnectionClosedError:
                logger.warning("client_recv_conn_closed from=%s:%d", host, port)

        return None

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

    def stop(self) -> None:
        self._running = False
        try:
            self._server.close()
        except OSError:
            pass
        self._executor.shutdown(wait=False, cancel_futures=True)
        if self._accept_thread and self._accept_thread.is_alive():
            self._accept_thread.join(timeout=1.0)

    def _accept_loop(self) -> None:
        while self._running:
            try:
                conn, addr = self._server.accept()
                conn.settimeout(30.0)  # 30s timeout per spec
                self._executor.submit(self._handle_client, conn, addr[0])
            except OSError:
                if not self._running:
                    break

    def _handle_client(self, conn: socket.socket, ip: str) -> None:
        try:
            with conn:
                raw = recv_framed(conn)
                if not raw:
                    return
                    
                msg = decode_message(raw)
                response = self.handler(ip, msg)
                
                if response:
                    send_framed(conn, encode_message(response))
        except (FramingError, ProtocolError) as e:
            logger.warning("Protocol error from %s: %s", ip, e)
        except socket.timeout:
            logger.warning("Socket timeout from %s", ip)
        except ConnectionClosedError:
            pass  # Normal closure
        except Exception as e:
            logger.error("Unexpected error handling client %s: %s", ip, e)


class TCPClient:
    """A client for sending single messages and optionally receiving a response."""
    
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def send_and_receive(self, host: str, port: int, msg: NetworkMessage) -> NetworkMessage | None:
        """Send a message and wait for an optional response."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout)
            sock.connect((host, port))
            
            send_framed(sock, encode_message(msg))
            
            try:
                raw = recv_framed(sock)
                if raw:
                    return decode_message(raw)
            except ConnectionClosedError:
                pass
                
        return None

"""
Long-running peer process for multi-instance E2E tests.

Each peer exposes a small HTTP control plane (health, pubkey, roster, send, inbox)
while running BroadcastNode + SecureChatSession on WebSockets.

Run:
    python -m e2e.peer_worker --port 5201 --control-port 15201
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from distribution import BroadcastNode, InMemoryRegistry, Message
from security import SecureChatSession

logger = logging.getLogger(__name__)


class PeerRuntime:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.address = f"{host}:{port}"
        self.registry = InMemoryRegistry()
        self.session = SecureChatSession(user_id=self.address)
        self.node = BroadcastNode(host, port, self.registry)
        self._received: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._shutdown = threading.Event()

        self.node.on_message = self._on_message

    def _on_message(self, msg: Message) -> None:
        plaintext = self.session.open_incoming(msg)
        if plaintext is None:
            return
        with self._lock:
            self._received.append(
                {
                    "id": msg.id,
                    "sender": msg.sender,
                    "plaintext": plaintext,
                    "content_wire": msg.content,
                }
            )

    def start(self) -> None:
        self.node.start()

    def stop(self) -> None:
        self.node.stop()
        self._shutdown.set()

    def add_peer_address(self, host: str, port: int) -> None:
        self.registry.add_peer(host, port)

    def register_roster(self, peers: dict[str, str]) -> None:
        """peers: user_id -> base64-encoded public key PEM."""
        for user_id, pem_b64 in peers.items():
            pem = base64.b64decode(pem_b64.encode("ascii"))
            self.session.register_peer(user_id, pem)

    def send_plaintext(self, plaintext: str) -> dict[str, Any]:
        msg = self.session.prepare_outgoing(
            plaintext=plaintext,
            sender_address=self.address,
        )
        self.node.broadcast(msg)
        return {"id": msg.id, "sender": msg.sender}

    def inbox_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._received)

    def clear_inbox(self) -> None:
        with self._lock:
            self._received.clear()


def _make_handler(runtime: PeerRuntime):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args) -> None:
            logger.debug(fmt, *args)

        def _json_response(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/health":
                self._json_response(
                    200,
                    {
                        "ok": True,
                        "address": runtime.address,
                        "ws_port": runtime.port,
                    },
                )
                return
            if path == "/pubkey":
                pem = runtime.session.public_key_pem
                self._json_response(
                    200,
                    {
                        "user_id": runtime.address,
                        "public_key_pem_b64": base64.b64encode(pem).decode("ascii"),
                    },
                )
                return
            if path == "/messages":
                self._json_response(200, {"messages": runtime.inbox_snapshot()})
                return
            self._json_response(404, {"error": "not found"})

        def do_POST(self) -> None:
            path = urlparse(self.path).path
            body = self._read_json()
            if path == "/roster":
                runtime.register_roster(body.get("peers", {}))
                self._json_response(200, {"ok": True, "count": len(body.get("peers", {}))})
                return
            if path == "/send":
                plaintext = body.get("plaintext", "")
                result = runtime.send_plaintext(str(plaintext))
                self._json_response(200, {"ok": True, **result})
                return
            if path == "/peers":
                for peer in body.get("peers", []):
                    runtime.add_peer_address(peer["host"], int(peer["port"]))
                self._json_response(200, {"ok": True})
                return
            if path == "/clear":
                runtime.clear_inbox()
                self._json_response(200, {"ok": True})
                return
            if path == "/shutdown":
                self._json_response(200, {"ok": True})
                threading.Thread(target=runtime.stop, daemon=True).start()
                return
            self._json_response(404, {"error": "not found"})

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PeerChat E2E peer worker")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--control-port", type=int, required=True)
    parser.add_argument("--log-level", default="WARNING")
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.WARNING))

    runtime = PeerRuntime(args.host, args.port)
    runtime.start()

    server = ThreadingHTTPServer((args.host, args.control_port), _make_handler(runtime))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    logger.warning(
        "peer %s ws=ws://%s:%d control=http://%s:%d",
        runtime.address,
        args.host,
        args.port,
        args.host,
        args.control_port,
    )

    try:
        runtime._shutdown.wait()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        runtime.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())

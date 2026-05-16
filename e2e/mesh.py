"""
Orchestrate multiple peer_worker subprocesses on one machine (SDET harness).

Similar in spirit to Docker Compose: N isolated peers, one coordinator drives
roster sync, sends messages, and asserts on inboxes — all code-driven.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PeerInstance:
    host: str
    ws_port: int
    control_port: int
    process: subprocess.Popen | None = None

    @property
    def address(self) -> str:
        return f"{self.host}:{self.ws_port}"

    @property
    def control_base(self) -> str:
        return f"http://{self.host}:{self.control_port}"


@dataclass
class PeerMesh:
    """Manage a local mesh of peer_worker processes."""

    host: str = "127.0.0.1"
    base_ws_port: int = 5201
    base_control_port: int = 15201
    peer_count: int = 3
    startup_timeout: float = 15.0
    peers: list[PeerInstance] = field(default_factory=list)
    _log_files: list[Any] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.peers:
            self.peers = [
                PeerInstance(
                    host=self.host,
                    ws_port=self.base_ws_port + i,
                    control_port=self.base_control_port + i,
                )
                for i in range(self.peer_count)
            ]

    def start(self, *, log_dir: str | None = None) -> None:
        repo_root = _repo_root()
        for peer in self.peers:
            stdout = stderr = subprocess.DEVNULL
            if log_dir:
                path = f"{log_dir}/peer_{peer.ws_port}.log"
                fh = open(path, "w", encoding="utf-8")
                self._log_files.append(fh)
                stdout = stderr = fh

            peer.process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "e2e.peer_worker",
                    "--host",
                    peer.host,
                    "--port",
                    str(peer.ws_port),
                    "--control-port",
                    str(peer.control_port),
                    "--log-level",
                    "WARNING",
                ],
                cwd=repo_root,
                stdout=stdout,
                stderr=stderr,
            )

        deadline = time.time() + self.startup_timeout
        for peer in self.peers:
            _wait_until(lambda: _health(peer), deadline, f"peer {peer.address} health")

        self._register_ws_peers()
        self.sync_rosters()

    def stop(self) -> None:
        for peer in self.peers:
            try:
                _post(peer, "/shutdown", {})
            except Exception:
                pass
        for peer in self.peers:
            if peer.process and peer.process.poll() is None:
                peer.process.terminate()
                try:
                    peer.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    peer.process.kill()
        for fh in self._log_files:
            fh.close()
        self._log_files.clear()

    def _register_ws_peers(self) -> None:
        peer_specs = [
            {"host": p.host, "port": p.ws_port} for p in self.peers
        ]
        for peer in self.peers:
            _post(peer, "/peers", {"peers": peer_specs})

    def sync_rosters(self) -> None:
        """Fetch each peer's pubkey and push full roster to every peer."""
        pubkeys: dict[str, str] = {}
        for peer in self.peers:
            data = _get(peer, "/pubkey")
            pubkeys[data["user_id"]] = data["public_key_pem_b64"]

        for peer in self.peers:
            _post(peer, "/roster", {"peers": pubkeys})

    def clear_all_inboxes(self) -> None:
        for peer in self.peers:
            _post(peer, "/clear", {})

    def send_from(self, peer_index: int, plaintext: str) -> dict[str, Any]:
        return _post(self.peers[peer_index], "/send", {"plaintext": plaintext})

    def wait_for_plaintext(
        self,
        plaintext: str,
        *,
        expected_count: int | None = None,
        timeout: float = 10.0,
        poll_interval: float = 0.2,
    ) -> dict[str, list[dict[str, Any]]]:
        """
        Poll until plaintext appears on peers.

        expected_count defaults to len(peers) if None.
        Returns {address: [matching messages]}.
        """
        if expected_count is None:
            expected_count = len(self.peers)

        deadline = time.time() + timeout
        while time.time() < deadline:
            results = self._collect_matching(plaintext)
            total = sum(len(v) for v in results.values())
            if total >= expected_count:
                return results
            time.sleep(poll_interval)

        results = self._collect_matching(plaintext)
        total = sum(len(v) for v in results.values())
        raise TimeoutError(
            f"expected >={expected_count} deliveries of {plaintext!r}, got {total}: {results}"
        )

    def _collect_matching(self, plaintext: str) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for peer in self.peers:
            data = _get(peer, "/messages")
            matches = [
                m for m in data.get("messages", []) if m.get("plaintext") == plaintext
            ]
            if matches:
                out[peer.address] = matches
        return out

    def assert_encrypted_on_wire(self, plaintext: str) -> None:
        """Sanity check: stored wire content is not equal to plaintext."""
        for peer in self.peers:
            data = _get(peer, "/messages")
            for msg in data.get("messages", []):
                if msg.get("plaintext") == plaintext:
                    wire = msg.get("content_wire", "")
                    assert plaintext not in wire
                    assert "pcrsa-h1" in wire or "boxes" in wire


def _repo_root() -> str:
    from pathlib import Path

    return str(Path(__file__).resolve().parents[1])


def _request(peer: PeerInstance, method: str, path: str, body: dict | None = None) -> dict:
    url = f"{peer.control_base}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get(peer: PeerInstance, path: str) -> dict:
    return _request(peer, "GET", path)


def _post(peer: PeerInstance, path: str, body: dict) -> dict:
    return _request(peer, "POST", path, body)


def _health(peer: PeerInstance) -> bool:
    try:
        data = _get(peer, "/health")
        return bool(data.get("ok"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return False


def _wait_until(predicate, deadline: float, label: str) -> None:
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.15)
    raise TimeoutError(f"timed out waiting for {label}")

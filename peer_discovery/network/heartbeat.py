"""Heartbeat and presence liveness maintenance."""
import logging
import socket
import threading
import time
from typing import TYPE_CHECKING

from peer_discovery.network.protocol import MessageType, NetworkMessage

if TYPE_CHECKING:
    from peer_discovery.network.discovery_node import DiscoveryNode

logger = logging.getLogger(__name__)


class HeartbeatManager:
    def __init__(self, node: 'DiscoveryNode'):
        self.node = node
        self._running = False
        self._heartbeat_thread: threading.Thread | None = None
        self._tick_thread: threading.Thread | None = None

    def start(self) -> None:
        self._running = True
        
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="heartbeat-out"
        )
        self._heartbeat_thread.start()
        
        self._tick_thread = threading.Thread(
            target=self._tick_loop,
            daemon=True,
            name="presence-tick"
        )
        self._tick_thread.start()

    def stop(self) -> None:
        self._running = False
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=1.0)
        if self._tick_thread and self._tick_thread.is_alive():
            self._tick_thread.join(timeout=1.0)

    def _heartbeat_loop(self) -> None:
        """Periodically broadcast heartbeats to all known active peers."""
        msg = NetworkMessage(
            message_type=MessageType.HEARTBEAT,
            sender_id=self.node.advertise_address,
            payload={}
        )
        # Encode once
        from peer_discovery.network.protocol import encode_message
        encoded_msg = encode_message(msg)
        
        from peer_discovery.network.framing import send_framed
        
        while self._running:
            try:
                snap = self.node.service.get_membership_snapshot()
                active_members = snap.get_active_members()
                
                for member_id in active_members:
                    if member_id == self.node.advertise_address:
                        continue
                        
                    try:
                        host, port_str = member_id.split(":")
                        port = int(port_str)
                    except ValueError:
                        continue
                        
                    # Send best-effort UDP-style over TCP (no retry, very short timeout)
                    self._send_ping(host, port, encoded_msg)
            except Exception as e:
                logger.error("Error in heartbeat loop: %s", e)
                
            time.sleep(self.node.config.heartbeat_interval)

    def _send_ping(self, host: str, port: int, encoded_msg: bytes) -> None:
        """Best effort ping."""
        import socket
        from peer_discovery.network.framing import send_framed
        
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1.0)
                sock.connect((host, port))
                send_framed(sock, encoded_msg)
        except Exception:
            pass  # Expected if peer is down or slow

    def _tick_loop(self) -> None:
        """Periodically trigger the coordinator's maintenance tick."""
        while self._running:
            try:
                self.node.service.tick()
            except Exception as e:
                logger.error("Error in tick loop: %s", e)
                
            time.sleep(self.node.config.tick_interval)


def handle_incoming_heartbeat(node: 'DiscoveryNode', msg: NetworkMessage) -> None:
    """Process an incoming HEARTBEAT message."""
    snap = node.service.get_membership_snapshot()
    
    # "Unknown-peer heartbeats: silently drops it and logs at DEBUG level"
    if msg.sender_id not in snap.members:
        logger.debug("Dropped heartbeat from unknown peer: %s", msg.sender_id)
        return
        
    node.service.heartbeat_member(msg.sender_id)

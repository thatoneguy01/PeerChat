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
        # Remember the last target count so we can log when it changes
        # without spamming on every tick.
        self._last_target_count = -1

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
        logger.info(
            "heartbeat_threads_started heartbeat_interval=%.1fs tick_interval=%.1fs",
            self.node.config.heartbeat_interval, self.node.config.tick_interval,
        )

    def stop(self) -> None:
        logger.info("heartbeat_stopping")
        self._running = False
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=1.0)
        if self._tick_thread and self._tick_thread.is_alive():
            self._tick_thread.join(timeout=1.0)
        logger.info("heartbeat_stopped")

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
                from peer_discovery.membership.models import MemberState
                # Heartbeat any peer we still expect to be reachable: ACTIVE
                # plus JOINING/BACKFILLING (mid-join), plus SUSPECTED (so a
                # heartbeat from them flips us back to RECONNECTED).
                trackable_states = {
                    MemberState.ACTIVE,
                    MemberState.JOINING,
                    MemberState.BACKFILLING,
                    MemberState.SUSPECTED,
                }
                trackable_members = [
                    uid for uid, m in snap.members.items()
                    if m.state in trackable_states
                ]

                targets = [m for m in trackable_members if m != self.node.advertise_address]
                if len(targets) != self._last_target_count:
                    logger.info(
                        "heartbeat_targets_changed prev=%d now=%d members=%s",
                        self._last_target_count, len(targets), targets,
                    )
                    self._last_target_count = len(targets)

                for member_id in targets:
                    try:
                        host, port_str = member_id.split(":")
                        port = int(port_str)
                    except ValueError:
                        logger.warning(
                            "heartbeat_bad_member_id member_id=%s — skipped", member_id,
                        )
                        continue

                    self._send_ping(host, port, encoded_msg)
            except Exception as e:
                logger.error("heartbeat_loop_error err=%s", e, exc_info=True)

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
                logger.debug("heartbeat_sent to=%s:%d", host, port)
        except Exception as e:
            logger.debug("heartbeat_failed to=%s:%d err=%s", host, port, e)

    def _tick_loop(self) -> None:
        """Periodically trigger the coordinator's maintenance tick."""
        while self._running:
            try:
                self.node.service.tick()
            except Exception as e:
                logger.error("tick_loop_error err=%s", e, exc_info=True)

            time.sleep(self.node.config.tick_interval)


def handle_incoming_heartbeat(node: 'DiscoveryNode', msg: NetworkMessage) -> None:
    """Process an incoming HEARTBEAT message."""
    snap = node.service.get_membership_snapshot()

    if msg.sender_id not in snap.members:
        logger.debug(
            "heartbeat_recv_unknown_peer sender=%s — dropping (not in membership)",
            msg.sender_id,
        )
        return

    logger.debug("heartbeat_recv from=%s", msg.sender_id)
    node.service.heartbeat_member(msg.sender_id)

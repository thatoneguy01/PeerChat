"""Heartbeat and presence liveness maintenance.

Heartbeats ride Distribution's broadcast — one signed
``SUBTYPE_HEARTBEAT`` Message per ``heartbeat_interval`` seconds (default
5.0s), fanned out to every reachable peer. The receiver's
``DiscoveryNode._handle_heartbeat`` (invoked via ``on_message``) records
the timestamp into the ``PresenceManager``, which drives the SWIM-style
two-phase ``ACTIVE → SUSPECTED → DISCONNECTED`` failure detector.

Two background threads run while the manager is active:

- ``heartbeat-out`` — emits one heartbeat envelope per interval.
- ``presence-tick`` — calls ``MembershipService.tick()`` every
  ``tick_interval`` seconds (default 1.0s). The tick is what actually
  fires DISCONNECT_SUSPECTED / DISCONNECT_TIMEOUT / backfill-timeout
  transitions; without it, presence state never advances even if peers
  stop heartbeating.

Both intervals are configurable via :class:`DiscoveryConfig`.
"""
import logging
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from peer_discovery.network.discovery_node import DiscoveryNode

logger = logging.getLogger(__name__)


class HeartbeatManager:
    """Runs two background threads:

    - ``heartbeat-out`` broadcasts a discovery_heartbeat envelope every
      ``heartbeat_interval`` seconds.
    - ``presence-tick`` calls ``MembershipService.tick()`` every
      ``tick_interval`` seconds to drive presence/backfill timeouts.
    """

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
            name="heartbeat-out",
        )
        self._heartbeat_thread.start()

        self._tick_thread = threading.Thread(
            target=self._tick_loop,
            daemon=True,
            name="presence-tick",
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
        """Broadcast one heartbeat per interval through Distribution."""
        while self._running:
            try:
                self._broadcast_heartbeat()
            except Exception as e:
                logger.error("heartbeat_loop_error err=%s", e, exc_info=True)

            time.sleep(self.node.config.heartbeat_interval)

    def _broadcast_heartbeat(self) -> None:
        if self.node.broadcast_node is None:
            return  # nothing to do; tests / standalone DiscoveryNode

        from distribution.message import Message
        from peer_discovery.membership.models import MemberState
        from peer_discovery.network.protocol import (
            SUBTYPE_HEARTBEAT, encode_discovery_envelope,
        )

        snap = self.node.service.get_membership_snapshot()
        trackable_states = {
            MemberState.ACTIVE, MemberState.JOINING,
            MemberState.BACKFILLING, MemberState.SUSPECTED,
        }
        targets = [
            uid for uid, m in snap.members.items()
            if m.state in trackable_states and uid != self.node.advertise_address
        ]
        if len(targets) != self._last_target_count:
            logger.info(
                "heartbeat_targets_changed prev=%d now=%d members=%s",
                self._last_target_count, len(targets), targets,
            )
            self._last_target_count = len(targets)

        if not targets:
            return

        content = encode_discovery_envelope(
            subtype=SUBTYPE_HEARTBEAT,
            sender_pub_pem=self.node.public_key_pem,
            payload={},
        )
        hb_msg = Message(content=content, sender=self.node.advertise_address)
        try:
            self.node.broadcast_node.broadcast(hb_msg)
            logger.debug("heartbeat_broadcast_sent targets=%d", len(targets))
        except Exception as exc:
            logger.debug("heartbeat_broadcast_failed err=%s", exc)

    def _tick_loop(self) -> None:
        """Periodically trigger the coordinator's maintenance tick."""
        while self._running:
            try:
                self.node.service.tick()
            except Exception as e:
                logger.error("tick_loop_error err=%s", e, exc_info=True)

            time.sleep(self.node.config.tick_interval)

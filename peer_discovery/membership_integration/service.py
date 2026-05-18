"""
MembershipService Facade (external API)
"""

import logging
import threading
from peer_discovery.membership_integration.coordinator import MembershipCoordinator
from peer_discovery.membership.models import (
    JoinResult, MembershipSnapshotData, SubscriptionHandle, ValidationResult
)
from typing import Callable

logger = logging.getLogger(__name__)


class MembershipService:
    """
    Public API for the Membership & Presence Service.
    This is the ONLY class external teams should import.
    7 methods + 1 registration hook. That's the entire surface.
    """

    def __init__(self, room_id: str, storage_dir: str | None = None, enable_tracing: bool = False,
                 local_user_id: str | None = None):
        self._coordinator = MembershipCoordinator(room_id, storage_dir, enable_tracing,
                                                  local_user_id=local_user_id)
        self._coordinator.recover()
        self._tick_timer: threading.Timer | None = None
        self._tick_interval: float = 1.0
        self._tick_running = False

    def join_member(
        self,
        user_id: str,
        display_name: str,
        public_key: bytes | None = None,
        context: dict | None = None,
    ) -> JoinResult:
        return self._coordinator.handle_join(
            user_id, display_name, public_key=public_key, context=context
        )

    def leave_member(self, user_id: str) -> None:
        self._coordinator.handle_leave(user_id)

    def heartbeat_member(self, user_id: str) -> None:
        self._coordinator.handle_heartbeat(user_id)

    def get_membership_snapshot(self) -> MembershipSnapshotData:
        return self._coordinator.get_snapshot()

    def subscribe_membership_events(self, callback, from_version: int = 0) -> 'SubscriptionHandle':
        logger.info(
            "subscribe_membership_events from_version=%d callback=%s",
            from_version, getattr(callback, "__qualname__", repr(callback)),
        )
        return self._coordinator.subscribe(callback, from_version)

    def start_history_backfill(self, user_id: str) -> None:
        self._coordinator.handle_start_backfill(user_id)

    def complete_history_backfill(self, user_id: str) -> None:
        self._coordinator.handle_complete_backfill(user_id)

    def register_join_validator(self, validator: Callable) -> None:
        self._coordinator.register_join_validator(validator)

    def register_history_handler(self, handler: Callable) -> None:
        """Register the History team's backfill handler.

        Called as ``handler(user_id, event)`` after each JOIN_ACCEPTED.
        The history team should replay messages, then call
        ``service.complete_history_backfill(user_id)`` when done.
        """
        self._coordinator.register_history_handler(handler)

    @property
    def has_history_handler(self) -> bool:
        """True if a History team handler has been registered."""
        return self._coordinator.has_history_handler

    def tick(self) -> None:
        """Periodic maintenance: presence liveness and backfill timeouts."""
        self._coordinator.tick()

    def start_tick_scheduler(self, interval_s: float = 1.0) -> None:
        """Start a background daemon thread that calls tick() every
        ``interval_s`` seconds.  Safe to call multiple times (no-op if
        already running).
        """
        if self._tick_running:
            return
        self._tick_interval = interval_s
        self._tick_running = True
        self._schedule_next_tick()
        logger.info("Tick scheduler started (interval=%.1fs)", interval_s)

    def stop_tick_scheduler(self) -> None:
        """Stop the background tick scheduler."""
        self._tick_running = False
        if self._tick_timer is not None:
            self._tick_timer.cancel()
            self._tick_timer = None
        logger.info("Tick scheduler stopped")

    def _schedule_next_tick(self) -> None:
        if not self._tick_running:
            return
        self._tick_timer = threading.Timer(self._tick_interval, self._run_tick)
        self._tick_timer.daemon = True
        self._tick_timer.start()

    def _run_tick(self) -> None:
        if not self._tick_running:
            return
        try:
            self._coordinator.tick()
        except Exception as e:
            logger.warning("Tick failed: %s", e)
        self._schedule_next_tick()

    def apply_remote_event(self, event: 'MembershipEvent') -> None:
        """Apply a gossiped event from a remote peer."""
        self._coordinator._apply_remote_event(event)

    def apply_remote_snapshot(self, events: list['MembershipEvent']) -> None:
        """Apply a batch of events from a SNAPSHOT_RESPONSE."""
        self._coordinator._apply_remote_snapshot(events)

"""
MembershipService Facade (external API)
"""

from peer_discovery.membership_integration.coordinator import MembershipCoordinator
from peer_discovery.membership.models import (
    JoinResult, MembershipSnapshotData, SubscriptionHandle, ValidationResult
)
from typing import Callable


class MembershipService:
    """
    Public API for the Membership & Presence Service.
    This is the ONLY class external teams should import.
    7 methods + 1 registration hook. That's the entire surface.
    """

    def __init__(self, room_id: str, storage_dir: str | None = None, enable_tracing: bool = False):
        self._coordinator = MembershipCoordinator(room_id, storage_dir, enable_tracing)
        self._coordinator.recover()

    def join_member(self, user_id: str, display_name: str) -> JoinResult:
        return self._coordinator.handle_join(user_id, display_name)

    def leave_member(self, user_id: str) -> None:
        self._coordinator.handle_leave(user_id)

    def heartbeat_member(self, user_id: str) -> None:
        self._coordinator.handle_heartbeat(user_id)

    def get_membership_snapshot(self) -> MembershipSnapshotData:
        return self._coordinator.get_snapshot()

    def subscribe_membership_events(self, callback, from_version: int = 0) -> 'SubscriptionHandle':
        return self._coordinator.subscribe(callback, from_version)

    def start_history_backfill(self, user_id: str) -> None:
        self._coordinator.handle_start_backfill(user_id)

    def complete_history_backfill(self, user_id: str) -> None:
        self._coordinator.handle_complete_backfill(user_id)

    def register_join_validator(self, validator: Callable) -> None:
        self._coordinator.register_join_validator(validator)

    def tick(self) -> None:
        """Periodic maintenance: presence liveness and backfill timeouts."""
        self._coordinator.tick()

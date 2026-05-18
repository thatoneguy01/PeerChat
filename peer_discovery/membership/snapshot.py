"""Materialized membership view derived from the event log.

The snapshot is a pure projection of :class:`MembershipEventLog`: given
the same log, every node materializes the same snapshot. The snapshot
holds, per ``user_id``, a :class:`MemberInfo` recording the member's
current :class:`MemberState`, display name, public key (PEM bytes), and
last-heartbeat timestamp.

This module enforces the **state-machine guards** for every event type
(see ``_ALLOWED_FROM_STATES`` below). An event whose "from" state isn't
permitted produces an :class:`InvalidTransitionError` instead of silently
corrupting state — this is the safety net that catches misordered gossip
or stale catch-up replay.

Snapshots are O(1) to read (``get_member`` / ``get_active_members``) and
O(events) to materialize from cold via :meth:`apply`. The single
``version`` counter advances per applied event, and subscribers use
``version`` to subscribe-with-from-version semantics (see
:class:`peer_discovery.membership_integration.notifier.EventNotifier`).
"""
import copy
import logging
import threading
from typing import Any

from .models import (
    EventType,
    MemberInfo,
    MemberState,
    MembershipDelta,
    MembershipEvent,
    MembershipSnapshotData,
)

logger = logging.getLogger(__name__)


# Allowed "from" states for each event. None means no precondition on member state.
# A user_id with no current record is treated as state=None for matching.
_ALLOWED_FROM_STATES: dict[EventType, set[MemberState] | None] = {
    EventType.JOIN_REQUESTED: None,
    EventType.JOIN_ACCEPTED: {MemberState.DISCONNECTED, MemberState.LEFT},  # None handled separately
    EventType.JOIN_REJECTED: None,
    EventType.HISTORY_BACKFILL_STARTED: {MemberState.JOINING},
    EventType.HISTORY_BACKFILL_COMPLETE: {MemberState.BACKFILLING},
    EventType.LEAVE_REQUESTED: {MemberState.ACTIVE},
    EventType.LEAVE_CONFIRMED: {MemberState.LEAVING},
    EventType.HEARTBEAT: None,
    EventType.DISCONNECT_SUSPECTED: {MemberState.ACTIVE},
    EventType.DISCONNECT_TIMEOUT: {MemberState.SUSPECTED, MemberState.BACKFILLING},
    EventType.RECONNECTED: {MemberState.SUSPECTED},
}


class MembershipSnapshot:
    """Deterministic materialized view of the membership event log.

    Given the same event sequence, this always produces the same state.
    No randomness, no implicit time. The only timestamp source is the
    event itself (set by the coordinator on append).
    """

    def __init__(self, room_id: str):
        self._room_id = room_id
        self._members: dict[str, MemberInfo] = {}
        self._version: int = 0
        self._as_of_seq_no: int = 0
        self._lock = threading.RLock()

    @property
    def room_id(self) -> str:
        return self._room_id

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    @property
    def as_of_seq_no(self) -> int:
        with self._lock:
            return self._as_of_seq_no

    def apply_event(self, event: MembershipEvent) -> MembershipDelta | None:
        with self._lock:
            if event.seq_no <= self._as_of_seq_no:
                return None

            current = self._members.get(event.user_id)
            current_state = current.state if current else None

            if not self._is_transition_valid(event.event_type, current_state):
                logger.warning(
                    "Invalid transition: %s for user=%s in state=%s (seq_no=%d) — skipped",
                    event.event_type.value,
                    event.user_id,
                    current_state.value if current_state else "<absent>",
                    event.seq_no,
                )
                return None

            delta = self._dispatch(event, current)
            self._version = event.membership_version
            self._as_of_seq_no = event.seq_no
            return delta

    def _is_transition_valid(
        self, event_type: EventType, current_state: MemberState | None
    ) -> bool:
        if event_type == EventType.JOIN_ACCEPTED:
            return current_state in (None, MemberState.DISCONNECTED, MemberState.LEFT)

        if event_type == EventType.HEARTBEAT:
            # Only meaningful if member exists. Unknown → ignore silently (no warning).
            return current_state is not None

        allowed = _ALLOWED_FROM_STATES.get(event_type)
        if allowed is None:
            return True
        return current_state in allowed

    def _dispatch(
        self, event: MembershipEvent, current: MemberInfo | None
    ) -> MembershipDelta | None:
        et = event.event_type
        uid = event.user_id

        if et == EventType.JOIN_REQUESTED:
            return None

        if et == EventType.JOIN_REJECTED:
            return None

        if et == EventType.JOIN_ACCEPTED:
            self._members[uid] = MemberInfo(
                user_id=uid,
                display_name=event.display_name,
                state=MemberState.JOINING,
                joined_at=event.timestamp,
                last_heartbeat=event.timestamp,
                membership_version=event.membership_version,
                public_key=event.public_key,
            )
            return MembershipDelta(type="joined", user_id=uid, event=event)

        if et == EventType.HISTORY_BACKFILL_STARTED:
            current.state = MemberState.BACKFILLING
            current.membership_version = event.membership_version
            return None

        if et == EventType.HISTORY_BACKFILL_COMPLETE:
            current.state = MemberState.ACTIVE
            current.membership_version = event.membership_version
            return MembershipDelta(type="active", user_id=uid, event=event)

        if et == EventType.LEAVE_REQUESTED:
            current.state = MemberState.LEAVING
            current.membership_version = event.membership_version
            return None

        if et == EventType.LEAVE_CONFIRMED:
            current.state = MemberState.LEFT
            current.membership_version = event.membership_version
            return MembershipDelta(type="left", user_id=uid, event=event)

        if et == EventType.HEARTBEAT:
            current.last_heartbeat = event.timestamp
            return None

        if et == EventType.DISCONNECT_SUSPECTED:
            current.state = MemberState.SUSPECTED
            current.membership_version = event.membership_version
            return MembershipDelta(type="suspected", user_id=uid, event=event)

        if et == EventType.DISCONNECT_TIMEOUT:
            current.state = MemberState.DISCONNECTED
            current.membership_version = event.membership_version
            return MembershipDelta(type="disconnected", user_id=uid, event=event)

        if et == EventType.RECONNECTED:
            current.state = MemberState.ACTIVE
            current.last_heartbeat = event.timestamp
            current.membership_version = event.membership_version
            return MembershipDelta(type="reconnected", user_id=uid, event=event)

        return None

    def get_snapshot(self) -> MembershipSnapshotData:
        with self._lock:
            members_copy = {uid: copy.copy(m) for uid, m in self._members.items()}
            active_count = sum(
                1 for m in members_copy.values() if m.state == MemberState.ACTIVE
            )
            return MembershipSnapshotData(
                room_id=self._room_id,
                version=self._version,
                members=members_copy,
                active_count=active_count,
                as_of_seq_no=self._as_of_seq_no,
            )

    def get_member(self, user_id: str) -> MemberInfo | None:
        with self._lock:
            m = self._members.get(user_id)
            return copy.copy(m) if m else None

    def get_active_members(self) -> list[MemberInfo]:
        with self._lock:
            return [
                copy.copy(m)
                for m in self._members.values()
                if m.state == MemberState.ACTIVE
            ]

    def serialize(self) -> dict[str, Any]:
        with self._lock:
            return {
                "room_id": self._room_id,
                "version": self._version,
                "as_of_seq_no": self._as_of_seq_no,
                "members": {uid: m.to_dict() for uid, m in self._members.items()},
            }

    @classmethod
    def deserialize(cls, data: dict[str, Any]) -> "MembershipSnapshot":
        snap = cls(data["room_id"])
        snap._version = data["version"]
        snap._as_of_seq_no = data["as_of_seq_no"]
        snap._members = {
            uid: MemberInfo.from_dict(m) for uid, m in data["members"].items()
        }
        return snap

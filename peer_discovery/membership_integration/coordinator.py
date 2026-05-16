"""
MembershipCoordinator (orchestrator)
"""

import time
import logging
from peer_discovery.membership.models import *
from peer_discovery.membership.event_log import MembershipEventLog
from peer_discovery.membership.snapshot import MembershipSnapshot
from peer_discovery.membership.duplicate_guard import DuplicateGuard
from peer_discovery.membership.durability import DurabilityManager
from peer_discovery.membership_integration.notifier import EventNotifier
from peer_discovery.membership.presence import PresenceManager

logger = logging.getLogger(__name__)


# Event type → delta type used for catch-up replay. Only events that produce
# subscriber-visible deltas during live dispatch are listed; others (HEARTBEAT,
# JOIN_REQUESTED, LEAVE_REQUESTED, ...) are filtered out by the snapshot at
# live time and likewise skipped here.
_EVENT_TO_DELTA_TYPE: dict = {
    EventType.JOIN_ACCEPTED: "joined",
    EventType.HISTORY_BACKFILL_COMPLETE: "active",
    EventType.LEAVE_CONFIRMED: "left",
    EventType.DISCONNECT_SUSPECTED: "suspected",
    EventType.DISCONNECT_TIMEOUT: "disconnected",
    EventType.RECONNECTED: "reconnected",
}


def _synthesize_delta_for_event(event):
    delta_type = _EVENT_TO_DELTA_TYPE.get(event.event_type)
    if delta_type is None:
        return None
    return MembershipDelta(type=delta_type, user_id=event.user_id, event=event)


class MembershipCoordinator:
    BACKFILL_TIMEOUT_S = 30.0

    def __init__(self, room_id: str, storage_dir: str | None = None, enable_tracing: bool = False):
        self._room_id = room_id
        self._log = MembershipEventLog(room_id)
        self._snapshot = MembershipSnapshot(room_id)
        self._notifier = EventNotifier()
        self._duplicate_guard = DuplicateGuard()
        self._durability = DurabilityManager(storage_dir or ".")
        self._presence = PresenceManager(on_state_change=self._handle_presence_change)
        self._join_validator = None
        self._running = False

    def register_join_validator(self, validator) -> None:
        self._join_validator = validator

    def handle_join(self, user_id: str, display_name: str) -> JoinResult:
        # Duplicate join check
        if self._duplicate_guard.is_duplicate(user_id, EventType.JOIN_REQUESTED.value):
            return JoinResult(
                accepted=False,
                seq_no=0,
                membership_version=self._snapshot.version,
                active_members=self._snapshot.get_active_members(),
                reason="Duplicate join request"
            )

        # Security validator
        if self._join_validator:
            result = self._join_validator(user_id, display_name)
            if hasattr(result, 'accepted') and not result.accepted:
                event = self._log.append(
                    EventType.JOIN_REJECTED, user_id, source="coordinator", term=1, display_name=display_name
                )
                self._snapshot.apply_event(event)
                return JoinResult(
                    accepted=False,
                    seq_no=event.seq_no,
                    membership_version=event.membership_version,
                    active_members=self._snapshot.get_active_members(),
                    reason=getattr(result, 'reason', None)
                )

        # Accept join
        event = self._log.append(
            EventType.JOIN_ACCEPTED, user_id, source="coordinator", term=1, display_name=display_name
        )
        delta = self._snapshot.apply_event(event)
        self._notifier.dispatch(event, delta)
        self._presence.register_member(user_id)
        self._durability.maybe_checkpoint(self._log, self._snapshot)
        return JoinResult(
            accepted=True,
            seq_no=event.seq_no,
            membership_version=event.membership_version,
            active_members=self._snapshot.get_active_members(),
            reason=None
        )

    def handle_leave(self, user_id: str) -> None:
        current = self._snapshot.get_member(user_id)
        if not current or current.state in (MemberState.LEFT, MemberState.DISCONNECTED):
            return
            
        if current.state != MemberState.LEAVING:
            req_event = self._log.append(
                EventType.LEAVE_REQUESTED, user_id, source="coordinator", term=1, display_name=current.display_name
            )
            self._snapshot.apply_event(req_event)
            
        event = self._log.append(
            EventType.LEAVE_CONFIRMED, user_id, source="coordinator", term=1, display_name=current.display_name
        )
        delta = self._snapshot.apply_event(event)
        self._notifier.dispatch(event, delta)
        self._presence.unregister_member(user_id)
        self._durability.maybe_checkpoint(self._log, self._snapshot)

    def handle_heartbeat(self, user_id: str) -> None:
        current = self._snapshot.get_member(user_id)
        if not current:
            return
        event = self._log.append(
            EventType.HEARTBEAT, user_id, source="coordinator", term=1, display_name=current.display_name
        )
        delta = self._snapshot.apply_event(event)
        # Heartbeats do not trigger notifications
        self._presence.record_heartbeat(user_id)
        self._durability.maybe_checkpoint(self._log, self._snapshot)

    def handle_start_backfill(self, user_id: str) -> None:
        current = self._snapshot.get_member(user_id)
        if not current or current.state != MemberState.JOINING:
            return
        event = self._log.append(
            EventType.HISTORY_BACKFILL_STARTED, user_id, source="coordinator", term=1, display_name=current.display_name
        )
        delta = self._snapshot.apply_event(event)
        self._notifier.dispatch(event, delta)
        self._durability.maybe_checkpoint(self._log, self._snapshot)

    def handle_complete_backfill(self, user_id: str) -> None:
        current = self._snapshot.get_member(user_id)
        if not current or current.state != MemberState.BACKFILLING:
            return
        event = self._log.append(
            EventType.HISTORY_BACKFILL_COMPLETE, user_id, source="coordinator", term=1, display_name=current.display_name
        )
        delta = self._snapshot.apply_event(event)
        self._notifier.dispatch(event, delta)
        self._durability.maybe_checkpoint(self._log, self._snapshot)

    def get_snapshot(self) -> MembershipSnapshotData:
        return self._snapshot.get_snapshot()

    def subscribe(self, callback, from_version: int = 0) -> SubscriptionHandle:
        """Subscribe to membership events. If from_version > 0, the subscriber
        is first delivered a catch-up batch of events with membership_version
        greater than from_version. Events that produced no visible delta
        (HEARTBEAT, JOIN_REQUESTED, LEAVE_REQUESTED, ...) are skipped during
        catch-up, matching live-dispatch semantics.
        """
        handle = self._notifier.subscribe(callback, from_version)
        catchup_events = self._log.get_events_since(from_version)
        for event in catchup_events:
            delta = _synthesize_delta_for_event(event)
            if delta is None:
                continue
            try:
                callback(event, delta)
            except Exception as e:
                logger.warning("Catch-up callback failed: %s", e)
        return handle

    def unsubscribe(self, handle: SubscriptionHandle) -> None:
        self._notifier.unsubscribe(handle)

    def tick(self) -> None:
        """Periodic maintenance: drive presence failure detection and
        enforce backfill timeouts.

        Person C wires this into a scheduler (e.g., asyncio loop every ~1s).
        """
        self._presence.check_liveness()
        self._sweep_backfill_timeouts()

    def _sweep_backfill_timeouts(self) -> None:
        """Auto-disconnect members stuck in BACKFILLING longer than
        BACKFILL_TIMEOUT_S. If the History team fails to call
        complete_history_backfill(), this prevents members from being
        wedged forever.
        """
        now = time.time()
        snap = self._snapshot.get_snapshot()
        for uid, m in snap.members.items():
            if m.state != MemberState.BACKFILLING:
                continue
            if now - m.joined_at <= self.BACKFILL_TIMEOUT_S:
                continue
            event = self._log.append(
                EventType.DISCONNECT_TIMEOUT,
                uid,
                source="coordinator",
                term=1,
                display_name=m.display_name,
            )
            delta = self._snapshot.apply_event(event)
            self._notifier.dispatch(event, delta)
            self._presence.unregister_member(uid)
            self._durability.maybe_checkpoint(self._log, self._snapshot)

    def _handle_presence_change(self, user_id: str, change_type: str) -> None:
        """Callback from PresenceManager. Translates state changes into log
        appends + snapshot updates + subscriber notifications.
        """
        current = self._snapshot.get_member(user_id)
        if not current:
            return

        if change_type == "suspected":
            event_type = EventType.DISCONNECT_SUSPECTED
        elif change_type == "timeout":
            event_type = EventType.DISCONNECT_TIMEOUT
        elif change_type == "reconnected":
            event_type = EventType.RECONNECTED
        else:
            logger.warning("Unknown presence change_type: %s", change_type)
            return

        event = self._log.append(
            event_type,
            user_id,
            source="presence",
            term=1,
            display_name=current.display_name,
        )
        delta = self._snapshot.apply_event(event)
        self._notifier.dispatch(event, delta)

        if change_type == "timeout":
            self._presence.unregister_member(user_id)

        self._durability.maybe_checkpoint(self._log, self._snapshot)

    def recover(self) -> bool:
        # Recovery logic (Phase 4+)
        return True

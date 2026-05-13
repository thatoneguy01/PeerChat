import threading
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable


class PresenceState(Enum):
    ALIVE = "ALIVE"
    SUSPECTED = "SUSPECTED"
    DEAD = "DEAD"


@dataclass
class PresenceEntry:
    user_id: str
    last_heartbeat: float
    state: PresenceState
    suspected_at: float | None = None


class PresenceManager:
    """Heartbeat-based presence tracking with SWIM-style suspicion.

    Does NOT write to the event log. Reports state changes to the coordinator
    via the on_state_change callback. The coordinator's tick loop calls
    check_liveness() periodically.
    """

    DEFAULT_HEARTBEAT_INTERVAL_S: float = 5.0
    DEFAULT_SUSPECT_AFTER_MISSED: int = 3
    DEFAULT_DEAD_AFTER_SUSPECT_S: float = 15.0

    def __init__(
        self,
        on_state_change: Callable[[str, str], None],
        heartbeat_interval_s: float | None = None,
        suspect_after_missed: int | None = None,
        dead_after_suspect_s: float | None = None,
        time_fn: Callable[[], float] | None = None,
    ):
        """on_state_change(user_id, change_type) where change_type is one of:
        "suspected", "timeout", "reconnected".
        """
        self._members: dict[str, PresenceEntry] = {}
        self._on_state_change = on_state_change
        self._lock = threading.RLock()
        self._time_fn = time_fn or time.time

        self.heartbeat_interval_s = (
            heartbeat_interval_s
            if heartbeat_interval_s is not None
            else self.DEFAULT_HEARTBEAT_INTERVAL_S
        )
        self.suspect_after_missed = (
            suspect_after_missed
            if suspect_after_missed is not None
            else self.DEFAULT_SUSPECT_AFTER_MISSED
        )
        self.dead_after_suspect_s = (
            dead_after_suspect_s
            if dead_after_suspect_s is not None
            else self.DEFAULT_DEAD_AFTER_SUSPECT_S
        )

    def register_member(self, user_id: str) -> None:
        with self._lock:
            self._members[user_id] = PresenceEntry(
                user_id=user_id,
                last_heartbeat=self._time_fn(),
                state=PresenceState.ALIVE,
                suspected_at=None,
            )

    def unregister_member(self, user_id: str) -> None:
        with self._lock:
            self._members.pop(user_id, None)

    def record_heartbeat(self, user_id: str) -> None:
        with self._lock:
            entry = self._members.get(user_id)
            if not entry:
                return
            entry.last_heartbeat = self._time_fn()
            if entry.state == PresenceState.SUSPECTED:
                entry.state = PresenceState.ALIVE
                entry.suspected_at = None
                # Fire callback outside the lock to avoid re-entrancy hazards
                cb = self._on_state_change
                uid = user_id
            else:
                cb = None
                uid = None
        if cb is not None:
            cb(uid, "reconnected")

    def check_liveness(self) -> None:
        """Scan tracked members and detect failures. Called periodically by
        the coordinator's tick loop.
        """
        suspect_threshold = self.heartbeat_interval_s * self.suspect_after_missed
        pending_callbacks: list[tuple[str, str]] = []

        with self._lock:
            now = self._time_fn()
            for uid, entry in list(self._members.items()):
                if entry.state == PresenceState.ALIVE:
                    if now - entry.last_heartbeat > suspect_threshold:
                        entry.state = PresenceState.SUSPECTED
                        entry.suspected_at = now
                        pending_callbacks.append((uid, "suspected"))
                elif entry.state == PresenceState.SUSPECTED:
                    suspected_at = entry.suspected_at or now
                    if now - suspected_at > self.dead_after_suspect_s:
                        entry.state = PresenceState.DEAD
                        pending_callbacks.append((uid, "timeout"))

        for uid, change_type in pending_callbacks:
            self._on_state_change(uid, change_type)

    def get_member_presence(self, user_id: str) -> PresenceEntry | None:
        with self._lock:
            entry = self._members.get(user_id)
            return replace(entry) if entry else None

    def get_all_entries(self) -> dict[str, PresenceEntry]:
        with self._lock:
            return {uid: replace(entry) for uid, entry in self._members.items()}

    @property
    def tracked_count(self) -> int:
        with self._lock:
            return len(self._members)

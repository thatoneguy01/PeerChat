"""Heartbeat-driven presence tracking (SWIM-style suspicion)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable


class PresenceState(str, Enum):
    ALIVE = "ALIVE"
    SUSPECTED = "SUSPECTED"


@dataclass
class PresenceEntry:
    user_id: str
    last_heartbeat: float
    state: PresenceState = PresenceState.ALIVE
    suspected_at: float | None = None


class PresenceManager:
    """Tracks per-member heartbeats and reports suspect / timeout / reconnect."""

    def __init__(
        self,
        on_state_change: Callable[[str, str], None],
        time_fn: Callable[[], float] | None = None,
        heartbeat_interval_s: float = 5.0,
        suspect_after_missed: int = 3,
        dead_after_suspect_s: float = 15.0,
    ):
        self._on_state_change = on_state_change
        self._time = time_fn or time.time
        self.heartbeat_interval_s = heartbeat_interval_s
        self.suspect_after_missed = suspect_after_missed
        self.dead_after_suspect_s = dead_after_suspect_s
        self._members: dict[str, PresenceEntry] = {}

    @property
    def tracked_count(self) -> int:
        return len(self._members)

    def register_member(self, user_id: str) -> None:
        now = self._time()
        self._members[user_id] = PresenceEntry(user_id=user_id, last_heartbeat=now)

    def unregister_member(self, user_id: str) -> None:
        self._members.pop(user_id, None)

    def record_heartbeat(self, user_id: str) -> None:
        entry = self._members.get(user_id)
        if not entry:
            return
        now = self._time()
        entry.last_heartbeat = now
        if entry.state == PresenceState.SUSPECTED:
            entry.state = PresenceState.ALIVE
            entry.suspected_at = None
            self._on_state_change(user_id, "reconnected")

    def check_liveness(self) -> None:
        now = self._time()
        suspect_threshold = self.heartbeat_interval_s * self.suspect_after_missed
        for uid, entry in list(self._members.items()):
            if entry.state == PresenceState.ALIVE:
                if now - entry.last_heartbeat > suspect_threshold:
                    entry.state = PresenceState.SUSPECTED
                    entry.suspected_at = now
                    self._on_state_change(uid, "suspected")
            elif entry.state == PresenceState.SUSPECTED:
                if entry.suspected_at is not None and (
                    now - entry.suspected_at > self.dead_after_suspect_s
                ):
                    self._on_state_change(uid, "timeout")

    def get_member_presence(self, user_id: str) -> PresenceEntry | None:
        return self._members.get(user_id)

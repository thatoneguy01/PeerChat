import threading
import time
from typing import Any

from .exceptions import StaleTermError
from .models import EventType, MembershipEvent


class MembershipEventLog:
    """Append-only log of membership events. Source of truth.

    Only the Coordinator should call append(). Reads are safe from any thread.
    Thread-safe via an internal RLock; lock contention is negligible at
    control-plane traffic volumes.
    """

    def __init__(self, room_id: str):
        self._room_id = room_id
        self._log: list[MembershipEvent] = []
        self._next_seq_no: int = 1
        self._current_term: int = 1
        self._lock = threading.RLock()

    @property
    def room_id(self) -> str:
        return self._room_id

    def append(
        self,
        event_type: EventType,
        user_id: str,
        source: str,
        term: int,
        display_name: str = "",
        trace_id: str | None = None,
        public_key: bytes | None = None,
    ) -> MembershipEvent:
        with self._lock:
            if term < self._current_term:
                raise StaleTermError(
                    f"term {term} < current {self._current_term}"
                )
            if term > self._current_term:
                self._current_term = term

            event = MembershipEvent(
                seq_no=self._next_seq_no,
                room_id=self._room_id,
                user_id=user_id,
                event_type=event_type,
                timestamp=time.time(),
                membership_version=self._next_seq_no,
                source=source,
                term=self._current_term,
                trace_id=trace_id,
                display_name=display_name,
                public_key=public_key,
            )
            self._log.append(event)
            self._next_seq_no += 1
            return event

    def append_remote(self, event: MembershipEvent) -> MembershipEvent:
        """Append an event received from a remote peer, assigning a local seq_no."""
        with self._lock:
            # Term is advanced if the remote term is higher
            if event.term > self._current_term:
                self._current_term = event.term

            local_event = MembershipEvent(
                seq_no=self._next_seq_no,
                room_id=self._room_id,
                user_id=event.user_id,
                event_type=event.event_type,
                timestamp=event.timestamp,
                membership_version=self._next_seq_no,
                source="remote",
                term=self._current_term,
                trace_id=event.trace_id,
                display_name=event.display_name,
                originator=event.originator,
                public_key=event.public_key,
            )
            self._log.append(local_event)
            self._next_seq_no += 1
            return local_event

    def get_events_since(self, seq_no: int) -> list[MembershipEvent]:
        with self._lock:
            return [e for e in self._log if e.seq_no > seq_no]

    def get_latest_seq_no(self) -> int:
        with self._lock:
            return self._next_seq_no - 1

    def get_current_term(self) -> int:
        with self._lock:
            return self._current_term

    def advance_term(self, new_term: int) -> None:
        with self._lock:
            if new_term < self._current_term:
                raise StaleTermError(
                    f"cannot advance to term {new_term} < current {self._current_term}"
                )
            self._current_term = new_term

    def serialize(self) -> list[dict[str, Any]]:
        with self._lock:
            return [e.to_dict() for e in self._log]

    def serialize_since(self, seq_no: int) -> list[dict[str, Any]]:
        with self._lock:
            return [e.to_dict() for e in self._log if e.seq_no > seq_no]

    @classmethod
    def deserialize(cls, room_id: str, data: list[dict[str, Any]]) -> "MembershipEventLog":
        log = cls(room_id)
        events = [MembershipEvent.from_dict(d) for d in data]
        events.sort(key=lambda e: e.seq_no)
        log._log = events
        if events:
            log._next_seq_no = events[-1].seq_no + 1
            log._current_term = max(e.term for e in events)
        return log

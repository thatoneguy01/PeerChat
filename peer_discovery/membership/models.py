from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class MemberState(Enum):
    JOINING = "JOINING"
    BACKFILLING = "BACKFILLING"
    ACTIVE = "ACTIVE"
    SUSPECTED = "SUSPECTED"
    DISCONNECTED = "DISCONNECTED"
    LEAVING = "LEAVING"
    LEFT = "LEFT"


class EventType(Enum):
    JOIN_REQUESTED = "JOIN_REQUESTED"
    JOIN_ACCEPTED = "JOIN_ACCEPTED"
    JOIN_REJECTED = "JOIN_REJECTED"
    LEAVE_REQUESTED = "LEAVE_REQUESTED"
    LEAVE_CONFIRMED = "LEAVE_CONFIRMED"
    HEARTBEAT = "HEARTBEAT"
    DISCONNECT_SUSPECTED = "DISCONNECT_SUSPECTED"
    DISCONNECT_TIMEOUT = "DISCONNECT_TIMEOUT"
    RECONNECTED = "RECONNECTED"
    HISTORY_BACKFILL_STARTED = "HISTORY_BACKFILL_STARTED"
    HISTORY_BACKFILL_COMPLETE = "HISTORY_BACKFILL_COMPLETE"


@dataclass(frozen=True)
class MembershipEvent:
    seq_no: int
    room_id: str
    user_id: str
    event_type: EventType
    timestamp: float
    membership_version: int
    source: str
    term: int
    trace_id: str | None = None
    display_name: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq_no": self.seq_no,
            "room_id": self.room_id,
            "user_id": self.user_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "membership_version": self.membership_version,
            "source": self.source,
            "term": self.term,
            "trace_id": self.trace_id,
            "display_name": self.display_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MembershipEvent":
        return cls(
            seq_no=data["seq_no"],
            room_id=data["room_id"],
            user_id=data["user_id"],
            event_type=EventType(data["event_type"]),
            timestamp=data["timestamp"],
            membership_version=data["membership_version"],
            source=data["source"],
            term=data["term"],
            trace_id=data.get("trace_id"),
            display_name=data.get("display_name", ""),
        )


@dataclass
class MemberInfo:
    user_id: str
    display_name: str
    state: MemberState
    joined_at: float
    last_heartbeat: float
    membership_version: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "state": self.state.value,
            "joined_at": self.joined_at,
            "last_heartbeat": self.last_heartbeat,
            "membership_version": self.membership_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemberInfo":
        return cls(
            user_id=data["user_id"],
            display_name=data["display_name"],
            state=MemberState(data["state"]),
            joined_at=data["joined_at"],
            last_heartbeat=data["last_heartbeat"],
            membership_version=data["membership_version"],
        )


@dataclass(frozen=True)
class MembershipSnapshotData:
    room_id: str
    version: int
    members: dict[str, MemberInfo]
    active_count: int
    as_of_seq_no: int


@dataclass
class JoinResult:
    accepted: bool
    seq_no: int
    membership_version: int
    active_members: list[MemberInfo]
    reason: str | None = None


@dataclass
class MembershipDelta:
    type: str
    user_id: str
    event: MembershipEvent | None = None


@dataclass
class ValidationResult:
    accepted: bool
    reason: str | None = None


@dataclass
class SubscriptionHandle:
    id: str

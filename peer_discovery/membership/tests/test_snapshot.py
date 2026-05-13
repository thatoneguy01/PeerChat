import logging

import pytest

from peer_discovery.membership.models import (
    EventType,
    MemberState,
    MembershipEvent,
)
from peer_discovery.membership.snapshot import MembershipSnapshot


def evt(seq_no: int, user_id: str, event_type: EventType, **kw) -> MembershipEvent:
    return MembershipEvent(
        seq_no=seq_no,
        room_id="room-1",
        user_id=user_id,
        event_type=event_type,
        timestamp=float(seq_no),
        membership_version=seq_no,
        source="coord",
        term=1,
        trace_id=None,
        display_name=kw.get("display_name", ""),
    )


def test_full_join_lifecycle():
    s = MembershipSnapshot("room-1")
    d1 = s.apply_event(evt(1, "alice", EventType.JOIN_ACCEPTED, display_name="Alice"))
    assert d1.type == "joined"
    assert s.get_member("alice").state == MemberState.JOINING

    d2 = s.apply_event(evt(2, "alice", EventType.HISTORY_BACKFILL_STARTED))
    assert d2 is None
    assert s.get_member("alice").state == MemberState.BACKFILLING

    d3 = s.apply_event(evt(3, "alice", EventType.HISTORY_BACKFILL_COMPLETE))
    assert d3.type == "active"
    assert s.get_member("alice").state == MemberState.ACTIVE

    snap = s.get_snapshot()
    assert snap.active_count == 1
    assert snap.version == 3
    assert snap.as_of_seq_no == 3


def test_leave_lifecycle():
    s = MembershipSnapshot("room-1")
    s.apply_event(evt(1, "alice", EventType.JOIN_ACCEPTED))
    s.apply_event(evt(2, "alice", EventType.HISTORY_BACKFILL_STARTED))
    s.apply_event(evt(3, "alice", EventType.HISTORY_BACKFILL_COMPLETE))
    d_req = s.apply_event(evt(4, "alice", EventType.LEAVE_REQUESTED))
    assert d_req is None
    d_conf = s.apply_event(evt(5, "alice", EventType.LEAVE_CONFIRMED))
    assert d_conf.type == "left"
    assert s.get_member("alice").state == MemberState.LEFT


def test_disconnect_lifecycle():
    s = MembershipSnapshot("room-1")
    s.apply_event(evt(1, "alice", EventType.JOIN_ACCEPTED))
    s.apply_event(evt(2, "alice", EventType.HISTORY_BACKFILL_STARTED))
    s.apply_event(evt(3, "alice", EventType.HISTORY_BACKFILL_COMPLETE))
    d_s = s.apply_event(evt(4, "alice", EventType.DISCONNECT_SUSPECTED))
    assert d_s.type == "suspected"
    d_t = s.apply_event(evt(5, "alice", EventType.DISCONNECT_TIMEOUT))
    assert d_t.type == "disconnected"
    assert s.get_member("alice").state == MemberState.DISCONNECTED


def test_reconnect_from_suspected():
    s = MembershipSnapshot("room-1")
    s.apply_event(evt(1, "alice", EventType.JOIN_ACCEPTED))
    s.apply_event(evt(2, "alice", EventType.HISTORY_BACKFILL_STARTED))
    s.apply_event(evt(3, "alice", EventType.HISTORY_BACKFILL_COMPLETE))
    s.apply_event(evt(4, "alice", EventType.DISCONNECT_SUSPECTED))
    d = s.apply_event(evt(5, "alice", EventType.RECONNECTED))
    assert d.type == "reconnected"
    assert s.get_member("alice").state == MemberState.ACTIVE


def test_heartbeat_updates_last_seen_no_delta():
    s = MembershipSnapshot("room-1")
    s.apply_event(evt(1, "alice", EventType.JOIN_ACCEPTED))
    s.apply_event(evt(2, "alice", EventType.HISTORY_BACKFILL_STARTED))
    s.apply_event(evt(3, "alice", EventType.HISTORY_BACKFILL_COMPLETE))
    d = s.apply_event(evt(4, "alice", EventType.HEARTBEAT))
    assert d is None
    assert s.get_member("alice").last_heartbeat == 4.0


def test_frozen_copy_isolation():
    s = MembershipSnapshot("room-1")
    s.apply_event(evt(1, "alice", EventType.JOIN_ACCEPTED))
    snap = s.get_snapshot()
    snap.members["alice"].state = MemberState.LEFT
    snap.members.pop("alice", None)
    assert s.get_member("alice").state == MemberState.JOINING


def test_stale_event_is_noop():
    s = MembershipSnapshot("room-1")
    s.apply_event(evt(1, "alice", EventType.JOIN_ACCEPTED))
    s.apply_event(evt(2, "alice", EventType.HISTORY_BACKFILL_STARTED))
    # Re-feeding seq_no=1 should be a no-op
    d = s.apply_event(evt(1, "alice", EventType.JOIN_ACCEPTED))
    assert d is None
    assert s.as_of_seq_no == 2
    assert s.get_member("alice").state == MemberState.BACKFILLING


def test_invalid_transition_ignored_and_logged(caplog):
    s = MembershipSnapshot("room-1")
    s.apply_event(evt(1, "alice", EventType.JOIN_ACCEPTED))
    # In JOINING, LEAVE_CONFIRMED is invalid (requires LEAVING)
    with caplog.at_level(logging.WARNING):
        d = s.apply_event(evt(2, "alice", EventType.LEAVE_CONFIRMED))
    assert d is None
    assert s.get_member("alice").state == MemberState.JOINING
    assert s.as_of_seq_no == 1  # did not advance
    assert any("Invalid transition" in r.message for r in caplog.records)


def test_heartbeat_unknown_user_ignored():
    s = MembershipSnapshot("room-1")
    d = s.apply_event(evt(1, "ghost", EventType.HEARTBEAT))
    assert d is None
    assert s.as_of_seq_no == 0


def test_rejoin_after_disconnect():
    s = MembershipSnapshot("room-1")
    for seq, et in [
        (1, EventType.JOIN_ACCEPTED),
        (2, EventType.HISTORY_BACKFILL_STARTED),
        (3, EventType.HISTORY_BACKFILL_COMPLETE),
        (4, EventType.DISCONNECT_SUSPECTED),
        (5, EventType.DISCONNECT_TIMEOUT),
    ]:
        s.apply_event(evt(seq, "alice", et))
    # Now in DISCONNECTED → JOIN_ACCEPTED should be valid (rejoin)
    d = s.apply_event(evt(6, "alice", EventType.JOIN_ACCEPTED))
    assert d is not None and d.type == "joined"
    assert s.get_member("alice").state == MemberState.JOINING


def test_active_count():
    s = MembershipSnapshot("room-1")
    # Two users, only alice reaches ACTIVE
    s.apply_event(evt(1, "alice", EventType.JOIN_ACCEPTED))
    s.apply_event(evt(2, "alice", EventType.HISTORY_BACKFILL_STARTED))
    s.apply_event(evt(3, "alice", EventType.HISTORY_BACKFILL_COMPLETE))
    s.apply_event(evt(4, "bob", EventType.JOIN_ACCEPTED))
    snap = s.get_snapshot()
    assert snap.active_count == 1
    assert len(snap.members) == 2


def test_determinism_replay():
    events = [
        evt(1, "alice", EventType.JOIN_ACCEPTED),
        evt(2, "bob", EventType.JOIN_ACCEPTED),
        evt(3, "alice", EventType.HISTORY_BACKFILL_STARTED),
        evt(4, "alice", EventType.HISTORY_BACKFILL_COMPLETE),
        evt(5, "bob", EventType.HISTORY_BACKFILL_STARTED),
        evt(6, "bob", EventType.HISTORY_BACKFILL_COMPLETE),
        evt(7, "alice", EventType.DISCONNECT_SUSPECTED),
        evt(8, "alice", EventType.RECONNECTED),
    ]

    def build():
        s = MembershipSnapshot("room-1")
        for e in events:
            s.apply_event(e)
        return s.serialize()

    assert build() == build()


def test_snapshot_serialization_round_trip():
    s = MembershipSnapshot("room-1")
    s.apply_event(evt(1, "alice", EventType.JOIN_ACCEPTED))
    s.apply_event(evt(2, "alice", EventType.HISTORY_BACKFILL_STARTED))
    s.apply_event(evt(3, "alice", EventType.HISTORY_BACKFILL_COMPLETE))
    data = s.serialize()
    restored = MembershipSnapshot.deserialize(data)
    assert restored.serialize() == data
    assert restored.get_member("alice").state == MemberState.ACTIVE

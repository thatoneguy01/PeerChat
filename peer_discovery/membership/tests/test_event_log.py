from concurrent.futures import ThreadPoolExecutor

import pytest

from peer_discovery.membership.event_log import MembershipEventLog
from peer_discovery.membership.exceptions import StaleTermError
from peer_discovery.membership.models import EventType


def test_empty_log():
    log = MembershipEventLog("room-1")
    assert log.get_latest_seq_no() == 0
    assert log.get_events_since(0) == []
    assert log.get_current_term() == 1


def test_append_assigns_sequential_seq_nos():
    log = MembershipEventLog("room-1")
    for i in range(5):
        e = log.append(EventType.JOIN_ACCEPTED, f"u{i}", "coord", term=1)
        assert e.seq_no == i + 1
        assert e.membership_version == i + 1
        assert e.room_id == "room-1"
    assert log.get_latest_seq_no() == 5


def test_get_events_since():
    log = MembershipEventLog("room-1")
    for i in range(5):
        log.append(EventType.JOIN_ACCEPTED, f"u{i}", "coord", term=1)
    assert len(log.get_events_since(0)) == 5
    assert len(log.get_events_since(3)) == 2
    assert log.get_events_since(5) == []


def test_stale_term_rejected():
    log = MembershipEventLog("room-1")
    log.append(EventType.JOIN_ACCEPTED, "alice", "coord", term=5)
    with pytest.raises(StaleTermError):
        log.append(EventType.JOIN_ACCEPTED, "bob", "coord", term=4)


def test_higher_term_advances():
    log = MembershipEventLog("room-1")
    log.append(EventType.JOIN_ACCEPTED, "alice", "coord", term=1)
    log.append(EventType.JOIN_ACCEPTED, "bob", "coord", term=7)
    assert log.get_current_term() == 7
    with pytest.raises(StaleTermError):
        log.append(EventType.JOIN_ACCEPTED, "carol", "coord", term=6)


def test_display_name_passthrough():
    log = MembershipEventLog("room-1")
    e = log.append(
        EventType.JOIN_ACCEPTED, "alice", "coord", term=1, display_name="Alice"
    )
    assert e.display_name == "Alice"


def test_concurrent_appends():
    log = MembershipEventLog("room-1")
    N = 500
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(
            pool.map(
                lambda i: log.append(EventType.HEARTBEAT, f"u{i}", "coord", term=1),
                range(N),
            )
        )
    seq_nos = sorted(e.seq_no for e in log.get_events_since(0))
    assert seq_nos == list(range(1, N + 1))
    assert log.get_latest_seq_no() == N


def test_serialize_round_trip():
    log = MembershipEventLog("room-1")
    log.append(EventType.JOIN_ACCEPTED, "alice", "coord", term=2)
    log.append(EventType.HISTORY_BACKFILL_COMPLETE, "alice", "coord", term=2)
    log.append(EventType.LEAVE_CONFIRMED, "alice", "coord", term=3)

    data = log.serialize()
    restored = MembershipEventLog.deserialize("room-1", data)
    assert restored.get_latest_seq_no() == log.get_latest_seq_no()
    assert restored.get_current_term() == log.get_current_term()
    assert restored.serialize() == data


def test_serialize_since_returns_tail_only():
    log = MembershipEventLog("room-1")
    for i in range(5):
        log.append(EventType.JOIN_ACCEPTED, f"u{i}", "coord", term=1)
    tail = log.serialize_since(2)
    assert len(tail) == 3
    assert [d["seq_no"] for d in tail] == [3, 4, 5]


def test_advance_term_rejects_lower():
    log = MembershipEventLog("room-1")
    log.advance_term(5)
    assert log.get_current_term() == 5
    with pytest.raises(StaleTermError):
        log.advance_term(4)

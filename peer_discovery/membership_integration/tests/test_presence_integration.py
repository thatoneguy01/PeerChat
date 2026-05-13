"""End-to-end: PresenceManager → Coordinator → log/snapshot/notifier."""

import os

from peer_discovery.membership.models import EventType, MemberState
from peer_discovery.membership.presence import PresenceState
from peer_discovery.membership_integration.coordinator import MembershipCoordinator


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _make_coordinator(tmp_path, clock):
    c = MembershipCoordinator("room-1", storage_dir=str(tmp_path))
    # Replace the default PresenceManager with a fake-clocked one wired to the
    # same callback so tick-driven detection is deterministic.
    from peer_discovery.membership.presence import PresenceManager

    c._presence = PresenceManager(
        on_state_change=c._handle_presence_change,
        time_fn=clock,
        heartbeat_interval_s=1.0,
        suspect_after_missed=2,
        dead_after_suspect_s=3.0,
    )
    return c


def test_join_registers_member_with_presence(tmp_path):
    clock = FakeClock()
    c = _make_coordinator(tmp_path, clock)
    c.handle_join("alice", "Alice")
    assert c._presence.tracked_count == 1
    assert c._presence.get_member_presence("alice").state == PresenceState.ALIVE


def test_heartbeat_propagates_to_presence(tmp_path):
    clock = FakeClock()
    c = _make_coordinator(tmp_path, clock)
    c.handle_join("alice", "Alice")
    clock.advance(0.5)
    c.handle_heartbeat("alice")
    assert c._presence.get_member_presence("alice").last_heartbeat == 1000.5


def test_tick_drives_suspicion_through_snapshot(tmp_path):
    clock = FakeClock()
    c = _make_coordinator(tmp_path, clock)
    c.handle_join("alice", "Alice")
    c.handle_start_backfill("alice")
    c.handle_complete_backfill("alice")
    assert c.get_snapshot().members["alice"].state == MemberState.ACTIVE

    clock.advance(3.0)  # > 1.0 * 2 suspect threshold
    c.tick()
    snap = c.get_snapshot()
    assert snap.members["alice"].state == MemberState.SUSPECTED


def test_tick_drives_timeout_and_unregisters(tmp_path):
    clock = FakeClock()
    c = _make_coordinator(tmp_path, clock)
    c.handle_join("alice", "Alice")
    c.handle_start_backfill("alice")
    c.handle_complete_backfill("alice")

    clock.advance(3.0)
    c.tick()  # → suspected
    clock.advance(4.0)  # > 3.0 grace
    c.tick()  # → timeout

    snap = c.get_snapshot()
    assert snap.members["alice"].state == MemberState.DISCONNECTED
    # presence stops tracking after timeout
    assert c._presence.get_member_presence("alice") is None


def test_heartbeat_during_suspicion_triggers_reconnected(tmp_path):
    clock = FakeClock()
    c = _make_coordinator(tmp_path, clock)
    c.handle_join("alice", "Alice")
    c.handle_start_backfill("alice")
    c.handle_complete_backfill("alice")

    clock.advance(3.0)
    c.tick()  # → suspected
    assert c.get_snapshot().members["alice"].state == MemberState.SUSPECTED

    c.handle_heartbeat("alice")  # heartbeat updates presence → reconnected callback
    snap = c.get_snapshot()
    assert snap.members["alice"].state == MemberState.ACTIVE


def test_subscriber_sees_suspicion_and_timeout(tmp_path):
    clock = FakeClock()
    c = _make_coordinator(tmp_path, clock)
    events: list[tuple[str, str]] = []
    c.subscribe(lambda e, d: events.append((d.type, e.user_id)))

    c.handle_join("alice", "Alice")
    c.handle_start_backfill("alice")
    c.handle_complete_backfill("alice")
    clock.advance(3.0)
    c.tick()
    clock.advance(4.0)
    c.tick()

    types = [t for t, _ in events]
    assert "joined" in types
    assert "active" in types
    assert "suspected" in types
    assert "disconnected" in types


def test_leave_unregisters_from_presence(tmp_path):
    clock = FakeClock()
    c = _make_coordinator(tmp_path, clock)
    c.handle_join("alice", "Alice")
    c.handle_start_backfill("alice")
    c.handle_complete_backfill("alice")
    c.handle_leave("alice")
    assert c._presence.get_member_presence("alice") is None

"""Contract tests for Message Distribution team integration (Workstream D)."""

from __future__ import annotations

import shutil
import tempfile

import pytest

from peer_discovery.membership.models import (
    EventType,
    MemberState,
    MembershipDelta,
    MembershipEvent,
)
from peer_discovery.membership_integration.service import MembershipService


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def _install_clocked_presence(
    service: MembershipService,
    clock: FakeClock,
    *,
    heartbeat_interval_s: float = 1.0,
    suspect_after_missed: int = 1,
    dead_after_suspect_s: float = 10.0,
) -> None:
    from peer_discovery.membership.presence import PresenceManager

    service._coordinator._presence = PresenceManager(
        on_state_change=service._coordinator._handle_presence_change,
        time_fn=clock,
        heartbeat_interval_s=heartbeat_interval_s,
        suspect_after_missed=suspect_after_missed,
        dead_after_suspect_s=dead_after_suspect_s,
    )


class TestDistributionTeamRoutingContract:
    def setup_method(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self.service = MembershipService(room_id="test-room", storage_dir=self._tmpdir)
        self.received_events: list[tuple[MembershipEvent, MembershipDelta | None]] = []

        def capture_callback(
            event: MembershipEvent, delta: MembershipDelta | None
        ) -> None:
            self.received_events.append((event, delta))

        self.service.subscribe_membership_events(capture_callback)

    def teardown_method(self) -> None:
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_snapshot_returns_only_routable_members(self) -> None:
        self.service.join_member("alice", "Alice")
        self.service.start_history_backfill("alice")
        self.service.complete_history_backfill("alice")

        self.service.join_member("bob", "Bob")
        self.service.start_history_backfill("bob")

        snapshot = self.service.get_membership_snapshot()
        assert snapshot.members["alice"].state == MemberState.ACTIVE
        assert snapshot.members["bob"].state == MemberState.BACKFILLING

    def test_join_accepted_fires_before_active(self) -> None:
        self.service.join_member("alice", "Alice")
        self.service.start_history_backfill("alice")
        self.service.complete_history_backfill("alice")

        event_types = [
            e.event_type for e, _d in self.received_events if e.user_id == "alice"
        ]

        join_idx = event_types.index(EventType.JOIN_ACCEPTED)
        active_idx = event_types.index(EventType.HISTORY_BACKFILL_COMPLETE)
        assert join_idx < active_idx

    def test_leave_confirmed_fires_on_voluntary_leave(self) -> None:
        self.service.join_member("alice", "Alice")
        self.service.start_history_backfill("alice")
        self.service.complete_history_backfill("alice")
        self.service.leave_member("alice")

        leave_events = [
            e
            for e, _d in self.received_events
            if e.event_type == EventType.LEAVE_CONFIRMED and e.user_id == "alice"
        ]
        assert len(leave_events) == 1

    def test_disconnect_suspected_then_reconnected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = FakeClock(1000.0)
        monkeypatch.setattr(
            "peer_discovery.membership.event_log.time.time", lambda: clock()
        )
        monkeypatch.setattr(
            "peer_discovery.membership_integration.coordinator.time.time",
            lambda: clock(),
        )
        _install_clocked_presence(self.service, clock, dead_after_suspect_s=10.0)

        self.service.join_member("alice", "Alice")
        self.service.start_history_backfill("alice")
        self.service.complete_history_backfill("alice")

        self.service.heartbeat_member("alice")

        clock.advance(2.0)
        self.service.tick()

        self.service.heartbeat_member("alice")

        alice_events = [
            e.event_type for e, _d in self.received_events if e.user_id == "alice"
        ]
        suspected_idx = alice_events.index(EventType.DISCONNECT_SUSPECTED)
        reconnected_idx = alice_events.index(EventType.RECONNECTED)
        assert suspected_idx < reconnected_idx

    def test_disconnect_timeout_fires_after_suspected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = FakeClock(2000.0)
        monkeypatch.setattr(
            "peer_discovery.membership.event_log.time.time", lambda: clock()
        )
        monkeypatch.setattr(
            "peer_discovery.membership_integration.coordinator.time.time",
            lambda: clock(),
        )
        _install_clocked_presence(
            self.service, clock, dead_after_suspect_s=3.0
        )

        self.service.join_member("alice", "Alice")
        self.service.start_history_backfill("alice")
        self.service.complete_history_backfill("alice")

        self.service.heartbeat_member("alice")

        clock.advance(2.0)
        self.service.tick()

        clock.advance(4.0)
        self.service.tick()

        snapshot = self.service.get_membership_snapshot()
        assert snapshot.members["alice"].state == MemberState.DISCONNECTED

        alice_events = [
            e.event_type for e, _d in self.received_events if e.user_id == "alice"
        ]
        suspected_idx = alice_events.index(EventType.DISCONNECT_SUSPECTED)
        timeout_idx = alice_events.index(EventType.DISCONNECT_TIMEOUT)
        assert suspected_idx < timeout_idx

    def test_snapshot_version_increases_on_every_change(self) -> None:
        snapshot_before = self.service.get_membership_snapshot()
        self.service.join_member("alice", "Alice")
        snapshot_after = self.service.get_membership_snapshot()

        assert snapshot_after.version > snapshot_before.version

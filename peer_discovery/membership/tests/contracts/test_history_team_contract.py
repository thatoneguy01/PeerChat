"""Contract tests for Message History team integration (Workstream D)."""

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


class TestHistoryTeamBackfillContract:
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

    def test_full_backfill_handoff_protocol(self) -> None:
        result = self.service.join_member("alice", "Alice")
        assert result.accepted is True

        join_events = [
            e
            for e, _d in self.received_events
            if e.event_type == EventType.JOIN_ACCEPTED
        ]
        assert len(join_events) == 1
        assert join_events[0].user_id == "alice"

        self.service.start_history_backfill("alice")
        snapshot = self.service.get_membership_snapshot()
        assert snapshot.members["alice"].state == MemberState.BACKFILLING

        self.service.complete_history_backfill("alice")
        snapshot = self.service.get_membership_snapshot()
        assert snapshot.members["alice"].state == MemberState.ACTIVE

        complete_events = [
            e
            for e, _d in self.received_events
            if e.event_type == EventType.HISTORY_BACKFILL_COMPLETE
        ]
        assert len(complete_events) == 1

    def test_backfill_event_carries_required_fields(self) -> None:
        self.service.join_member("bob", "Bob")

        join_events = [
            e
            for e, _d in self.received_events
            if e.event_type == EventType.JOIN_ACCEPTED
        ]
        assert len(join_events) == 1
        event = join_events[0]

        assert event.user_id == "bob"
        assert event.room_id == "test-room"
        assert event.seq_no > 0
        assert event.timestamp > 0

    def test_backfill_timeout_auto_disconnects(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.service.join_member("charlie", "Charlie")
        self.service.start_history_backfill("charlie")

        snapshot = self.service.get_membership_snapshot()
        assert snapshot.members["charlie"].state == MemberState.BACKFILLING

        self.service._coordinator.BACKFILL_TIMEOUT_S = 0.1
        base = self.service.get_membership_snapshot().members["charlie"].joined_at
        monkeypatch.setattr(
            "peer_discovery.membership_integration.coordinator.time.time",
            lambda: base + 5.0,
        )
        self.service.tick()

        snapshot = self.service.get_membership_snapshot()
        assert snapshot.members["charlie"].state == MemberState.DISCONNECTED

        timeout_events = [
            e
            for e, _d in self.received_events
            if e.event_type == EventType.DISCONNECT_TIMEOUT and e.user_id == "charlie"
        ]
        assert len(timeout_events) == 1

    def test_complete_backfill_without_start_is_ignored(self) -> None:
        self.service.join_member("dave", "Dave")
        self.service.complete_history_backfill("dave")

        snapshot = self.service.get_membership_snapshot()
        assert snapshot.members["dave"].state == MemberState.JOINING

    def test_start_backfill_for_unknown_user_is_ignored(self) -> None:
        self.service.start_history_backfill("nonexistent")


class TestHistoryTeamRecoveryContract:
    def test_snapshot_contains_all_fields_for_recovery(self, tmp_path) -> None:
        service = MembershipService(room_id="test-room", storage_dir=str(tmp_path))
        service.join_member("alice", "Alice")
        service.start_history_backfill("alice")
        service.complete_history_backfill("alice")

        snapshot = service.get_membership_snapshot()

        assert snapshot.room_id == "test-room"
        assert snapshot.version > 0
        assert snapshot.as_of_seq_no > 0
        assert "alice" in snapshot.members
        assert snapshot.members["alice"].state == MemberState.ACTIVE
        assert snapshot.active_count == 1

    def test_subscribe_with_from_version_delivers_catchup(self, tmp_path) -> None:
        service = MembershipService(room_id="test-room", storage_dir=str(tmp_path))

        service.join_member("alice", "Alice")
        service.start_history_backfill("alice")
        service.complete_history_backfill("alice")

        snapshot = service.get_membership_snapshot()

        service.join_member("bob", "Bob")

        catchup_events: list[MembershipEvent] = []

        def capture(event: MembershipEvent, delta: MembershipDelta | None) -> None:
            catchup_events.append(event)

        service.subscribe_membership_events(capture, from_version=snapshot.version)

        bob_events = [e for e in catchup_events if e.user_id == "bob"]
        assert len(bob_events) > 0

    def test_members_in_backfilling_state_visible_in_snapshot(self, tmp_path) -> None:
        service = MembershipService(room_id="test-room", storage_dir=str(tmp_path))

        service.join_member("alice", "Alice")
        service.start_history_backfill("alice")

        snapshot = service.get_membership_snapshot()
        assert snapshot.members["alice"].state == MemberState.BACKFILLING

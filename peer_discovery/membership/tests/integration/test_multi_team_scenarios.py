"""
End-to-end integration tests simulating realistic multi-team usage (Workstream D).
"""

from __future__ import annotations

import pytest

from peer_discovery.membership.models import EventType, MemberState, MembershipEvent, ValidationResult
from peer_discovery.membership_integration.service import MembershipService


class TestFullRoomLifecycle:
    def test_member_joins_backfills_chats_then_leaves(self, tmp_path) -> None:
        service = MembershipService(room_id="test-room", storage_dir=str(tmp_path))

        def validator(user_id: str, display_name: str, context: dict) -> ValidationResult:
            return ValidationResult(accepted=True)

        service.register_join_validator(validator)

        history_events: list[MembershipEvent] = []
        distribution_events: list[MembershipEvent] = []
        security_events: list[MembershipEvent] = []

        def history_callback(event: MembershipEvent, delta) -> None:
            history_events.append(event)
            if event.event_type == EventType.JOIN_ACCEPTED:
                service.start_history_backfill(event.user_id)
                service.complete_history_backfill(event.user_id)

        def distribution_callback(event: MembershipEvent, delta) -> None:
            distribution_events.append(event)

        def security_callback(event: MembershipEvent, delta) -> None:
            security_events.append(event)

        service.subscribe_membership_events(history_callback)
        service.subscribe_membership_events(distribution_callback)
        service.subscribe_membership_events(security_callback)

        result = service.join_member("alice", "Alice")
        assert result.accepted is True

        for _ in range(5):
            service.heartbeat_member("alice")

        service.leave_member("alice")

        dist_types = [e.event_type for e in distribution_events]
        assert EventType.JOIN_ACCEPTED in dist_types
        assert EventType.HISTORY_BACKFILL_COMPLETE in dist_types
        assert EventType.LEAVE_CONFIRMED in dist_types

        sec_types = [e.event_type for e in security_events]
        assert EventType.JOIN_ACCEPTED in sec_types
        assert EventType.LEAVE_CONFIRMED in sec_types

    def test_multiple_members_concurrent_lifecycle(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        t = {"v": 10_000.0}

        def fake_time() -> float:
            return t["v"]

        monkeypatch.setattr(
            "peer_discovery.membership_integration.coordinator.time.time", fake_time
        )
        monkeypatch.setattr("peer_discovery.membership.presence.time.time", fake_time)
        monkeypatch.setattr("peer_discovery.membership.event_log.time.time", fake_time)

        service = MembershipService(room_id="test-room", storage_dir=str(tmp_path))
        p = service._coordinator._presence
        p.heartbeat_interval_s = 1.0
        p.suspect_after_missed = 1
        p.dead_after_suspect_s = 60.0

        service.join_member("alice", "Alice")
        service.start_history_backfill("alice")
        service.complete_history_backfill("alice")

        service.join_member("bob", "Bob")
        service.start_history_backfill("bob")

        service.join_member("charlie", "Charlie")
        service.start_history_backfill("charlie")
        service.complete_history_backfill("charlie")
        service.heartbeat_member("charlie")

        t["v"] += 5.0
        service.heartbeat_member("alice")
        service.heartbeat_member("bob")

        service.tick()

        snap = service.get_membership_snapshot()
        assert snap.members["alice"].state == MemberState.ACTIVE
        assert snap.members["bob"].state == MemberState.BACKFILLING
        assert snap.members["charlie"].state == MemberState.SUSPECTED

    def test_recovery_after_crash_all_teams_resubscribe(self, tmp_path) -> None:
        service = MembershipService(room_id="test-room", storage_dir=str(tmp_path))

        service.join_member("alice", "Alice")
        service.start_history_backfill("alice")
        service.complete_history_backfill("alice")

        snapshot = service.get_membership_snapshot()

        service.join_member("bob", "Bob")

        catchup: list[MembershipEvent] = []

        def capture(event: MembershipEvent, delta) -> None:
            catchup.append(event)

        service.subscribe_membership_events(capture, from_version=snapshot.version)

        bob_joins = [
            e
            for e in catchup
            if e.user_id == "bob" and e.event_type == EventType.JOIN_ACCEPTED
        ]
        assert len(bob_joins) >= 1

    def test_security_rejection_doesnt_leak_to_other_teams(self, tmp_path) -> None:
        service = MembershipService(room_id="test-room", storage_dir=str(tmp_path))
        received: list[MembershipEvent] = []

        def capture(event: MembershipEvent, delta) -> None:
            received.append(event)

        service.subscribe_membership_events(capture)

        def rejector(user_id: str, display_name: str, context: dict) -> ValidationResult:
            return ValidationResult(accepted=False, reason="blocked")

        service.register_join_validator(rejector)
        service.join_member("blocked_user", "Blocked")

        accepted = [e for e in received if e.event_type == EventType.JOIN_ACCEPTED]
        assert len(accepted) == 0

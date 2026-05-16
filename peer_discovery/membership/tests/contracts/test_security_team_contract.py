"""Contract tests for Security team integration (Workstream D)."""

from __future__ import annotations

import shutil
import tempfile

from peer_discovery.membership.models import (
    EventType,
    MemberState,
    MembershipDelta,
    MembershipEvent,
    ValidationResult,
)
from peer_discovery.membership_integration.service import MembershipService


class TestSecurityTeamValidatorContract:
    def test_validator_called_on_join(self) -> None:
        tmp = tempfile.mkdtemp()
        try:
            service = MembershipService(room_id="test-room", storage_dir=tmp)
            validator_calls: list[tuple[str, str]] = []

            def mock_validator(user_id: str, display_name: str, context: dict):
                validator_calls.append((user_id, display_name))
                return ValidationResult(accepted=True)

            service.register_join_validator(mock_validator)
            service.join_member("alice", "Alice")

            assert len(validator_calls) == 1
            assert validator_calls[0] == ("alice", "Alice")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_validator_rejection_prevents_join(self) -> None:
        tmp = tempfile.mkdtemp()
        try:
            service = MembershipService(room_id="test-room", storage_dir=tmp)
            received_events: list[MembershipEvent] = []

            def capture(event: MembershipEvent, delta: MembershipDelta | None) -> None:
                received_events.append(event)

            service.subscribe_membership_events(capture)

            def rejecting_validator(user_id: str, display_name: str, context: dict):
                return ValidationResult(accepted=False, reason="user_banned")

            service.register_join_validator(rejecting_validator)
            result = service.join_member("banned_user", "Banned")

            assert result.accepted is False
            assert result.reason == "user_banned"

            snapshot = service.get_membership_snapshot()
            assert snapshot.members.get("banned_user") is None

            reject_events = [
                e for e in received_events if e.event_type == EventType.JOIN_REJECTED
            ]
            assert len(reject_events) == 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_no_validator_means_all_joins_accepted(self, tmp_path) -> None:
        service = MembershipService(room_id="test-room", storage_dir=str(tmp_path))
        result = service.join_member("alice", "Alice")
        assert result.accepted is True


class TestSecurityTeamAuditContract:
    def test_audit_relevant_events_fire_correctly(self, tmp_path) -> None:
        service = MembershipService(room_id="test-room", storage_dir=str(tmp_path))
        audit_events: list[MembershipEvent] = []

        def audit_callback(event: MembershipEvent, delta: MembershipDelta | None) -> None:
            audit_events.append(event)

        service.subscribe_membership_events(audit_callback)

        service.join_member("alice", "Alice")
        service.start_history_backfill("alice")
        service.complete_history_backfill("alice")
        service.leave_member("alice")

        event_types = [e.event_type for e in audit_events]
        assert EventType.JOIN_ACCEPTED in event_types
        assert EventType.LEAVE_CONFIRMED in event_types

    def test_events_carry_trace_id_for_correlation(self, tmp_path) -> None:
        service = MembershipService(
            room_id="test-room", storage_dir=str(tmp_path), enable_tracing=True
        )
        received_events: list[MembershipEvent] = []

        def capture(event: MembershipEvent, delta: MembershipDelta | None) -> None:
            received_events.append(event)

        service.subscribe_membership_events(capture)
        service.join_member("alice", "Alice")

        join_events = [
            e for e in received_events if e.event_type == EventType.JOIN_ACCEPTED
        ]
        assert len(join_events) == 1
        assert join_events[0].trace_id is not None


class TestSecurityTeamForcedRemovalContract:
    def test_forced_removal_via_leave_member(self, tmp_path) -> None:
        service = MembershipService(room_id="test-room", storage_dir=str(tmp_path))
        service.join_member("bad_actor", "Bad Actor")
        service.start_history_backfill("bad_actor")
        service.complete_history_backfill("bad_actor")

        service.leave_member("bad_actor")

        snapshot = service.get_membership_snapshot()
        assert snapshot.members["bad_actor"].state == MemberState.LEFT

    def test_forced_removal_of_already_left_user_is_idempotent(self, tmp_path) -> None:
        service = MembershipService(room_id="test-room", storage_dir=str(tmp_path))
        service.join_member("alice", "Alice")
        service.start_history_backfill("alice")
        service.complete_history_backfill("alice")
        service.leave_member("alice")

        service.leave_member("alice")

    def test_snapshot_available_for_access_review(self, tmp_path) -> None:
        service = MembershipService(room_id="test-room", storage_dir=str(tmp_path))
        service.join_member("alice", "Alice")
        service.start_history_backfill("alice")
        service.complete_history_backfill("alice")

        snapshot = service.get_membership_snapshot()
        assert "alice" in snapshot.members
        assert snapshot.members["alice"].state == MemberState.ACTIVE
        assert snapshot.members["alice"].user_id == "alice"
        assert snapshot.members["alice"].display_name == "Alice"

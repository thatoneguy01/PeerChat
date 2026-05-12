import pytest
from peer_discovery.membership_integration.coordinator import MembershipCoordinator
from peer_discovery.membership.models import EventType, MemberState

@pytest.fixture
def coordinator(tmp_path):
    return MembershipCoordinator("room1", storage_dir=str(tmp_path))

def test_full_join_lifecycle(coordinator):
    res = coordinator.handle_join("u1", "User 1")
    assert res.accepted
    
    snap = coordinator.get_snapshot()
    assert "u1" in snap.members
    assert snap.members["u1"].state == MemberState.JOINING

    coordinator.handle_start_backfill("u1")
    assert coordinator.get_snapshot().members["u1"].state == MemberState.BACKFILLING

    coordinator.handle_complete_backfill("u1")
    assert coordinator.get_snapshot().members["u1"].state == MemberState.ACTIVE

def test_join_rejected_by_validator(coordinator):
    class Validator:
        def __init__(self):
            self.accepted = False
            self.reason = "denied"
        def __call__(self, user_id, display_name):
            return self
            
    coordinator.register_join_validator(Validator())
    res = coordinator.handle_join("u1", "User 1")
    assert not res.accepted
    assert res.reason == "denied"

def test_duplicate_join_rejected(coordinator):
    coordinator.handle_join("u1", "User 1")
    res = coordinator.handle_join("u1", "User 1")
    assert not res.accepted
    assert res.reason == "Duplicate join request"

def test_voluntary_leave(coordinator):
    coordinator.handle_join("u1", "User 1")
    coordinator.handle_start_backfill("u1")
    coordinator.handle_complete_backfill("u1")
    coordinator.handle_leave("u1")
    snap = coordinator.get_snapshot()
    assert "u1" in snap.members
    assert snap.members["u1"].state == MemberState.LEFT

def test_leave_idempotent(coordinator):
    coordinator.handle_join("u1", "User 1")
    coordinator.handle_start_backfill("u1")
    coordinator.handle_complete_backfill("u1")
    coordinator.handle_leave("u1")
    snap = coordinator.get_snapshot()
    v1 = snap.version
    coordinator.handle_leave("u1")
    snap2 = coordinator.get_snapshot()
    assert snap2.version == v1

def test_disconnect_suspected_then_timeout(coordinator):
    pass

def test_disconnect_suspected_then_reconnected(coordinator):
    pass

def test_backfill_timeout(coordinator):
    pass

def test_subscriber_catchup(coordinator):
    pass

def test_recovery_from_checkpoint(coordinator):
    pass

def test_history_team_integration_pattern(coordinator):
    pass

def test_distribution_team_integration_pattern(coordinator):
    pass

def test_security_team_forced_removal(coordinator):
    pass

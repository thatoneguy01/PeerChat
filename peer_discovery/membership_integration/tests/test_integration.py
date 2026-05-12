from peer_discovery.membership_integration.coordinator import MembershipCoordinator
from peer_discovery.membership.models import MemberState

def test_external_team_join_leave(tmp_path):
    coord = MembershipCoordinator("room1", storage_dir=str(tmp_path))
    coord.handle_join("u1", "User1")
    assert "u1" in coord.get_snapshot().members
    coord.handle_start_backfill("u1")
    coord.handle_complete_backfill("u1")
    coord.handle_leave("u1")
    assert coord.get_snapshot().members["u1"].state == MemberState.LEFT

def test_external_team_subscribe(tmp_path):
    coord = MembershipCoordinator("room1", storage_dir=str(tmp_path))
    events = []
    coord.subscribe(lambda e, d: events.append((e, d)))
    coord.handle_join("u1", "User1")
    assert len(events) == 1

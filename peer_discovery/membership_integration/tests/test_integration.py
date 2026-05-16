from peer_discovery.membership_integration.coordinator import MembershipCoordinator
from peer_discovery.membership_integration.service import MembershipService
from peer_discovery.membership.models import MemberState
import time

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

def test_tick_scheduler_starts_and_stops(tmp_path):
    """Verify the tick scheduler actually fires tick() in the background."""
    svc = MembershipService("room1", storage_dir=str(tmp_path))

    # Patch tick to count invocations
    tick_count = {"n": 0}
    original_tick = svc._coordinator.tick
    def counting_tick():
        tick_count["n"] += 1
        original_tick()
    svc._coordinator.tick = counting_tick

    svc.start_tick_scheduler(interval_s=0.05)
    time.sleep(0.25)  # should fire ~5 ticks
    svc.stop_tick_scheduler()

    assert tick_count["n"] >= 2  # at least a couple fired

    # Idempotent: calling start again should not crash
    svc.start_tick_scheduler(interval_s=0.05)
    svc.stop_tick_scheduler()


"""Tests for backfill-timeout sweep and subscription catch-up replay."""

import time

from peer_discovery.membership.models import EventType, MemberState
from peer_discovery.membership_integration.coordinator import (
    MembershipCoordinator,
    _synthesize_delta_for_event,
)


# ─── Catch-up replay ────────────────────────────────────────────────────


def test_subscribe_from_version_zero_replays_all_visible_events(tmp_path):
    c = MembershipCoordinator("room-1", storage_dir=str(tmp_path))
    c.handle_join("alice", "Alice")
    c.handle_start_backfill("alice")
    c.handle_complete_backfill("alice")
    c.handle_leave("alice")

    captured: list[tuple[str, str, int]] = []
    c.subscribe(
        lambda e, d: captured.append((e.event_type.value, d.type, e.membership_version)),
        from_version=0,
    )

    types = [t for _, t, _ in captured]
    assert "joined" in types
    assert "active" in types
    assert "left" in types
    # Non-visible events (HISTORY_BACKFILL_STARTED, LEAVE_REQUESTED) are filtered
    assert all(t in ("joined", "active", "left") for t in types)


def test_subscribe_with_from_version_skips_already_delivered(tmp_path):
    c = MembershipCoordinator("room-1", storage_dir=str(tmp_path))
    c.handle_join("alice", "Alice")
    c.handle_start_backfill("alice")
    c.handle_complete_backfill("alice")
    cutoff = c.get_snapshot().version  # everything up to here

    c.handle_join("bob", "Bob")
    c.handle_start_backfill("bob")
    c.handle_complete_backfill("bob")

    captured: list[str] = []
    c.subscribe(
        lambda e, d: captured.append(f"{d.type}:{e.user_id}"),
        from_version=cutoff,
    )

    # Only bob's events should replay; alice's are before the cutoff
    assert "joined:bob" in captured
    assert "active:bob" in captured
    assert not any("alice" in s for s in captured)


def test_subscribe_then_receives_live_events_after_catchup(tmp_path):
    c = MembershipCoordinator("room-1", storage_dir=str(tmp_path))
    c.handle_join("alice", "Alice")

    captured: list[str] = []
    c.subscribe(
        lambda e, d: captured.append(f"{d.type}:{e.user_id}"),
        from_version=0,
    )
    # Live event after subscription
    c.handle_join("bob", "Bob")

    assert "joined:alice" in captured  # from catch-up
    assert "joined:bob" in captured  # from live dispatch


def test_synthesize_delta_filters_non_visible_events():
    from peer_discovery.membership.models import MembershipEvent

    invisible = MembershipEvent(
        seq_no=1,
        room_id="r",
        user_id="u",
        event_type=EventType.HEARTBEAT,
        timestamp=0.0,
        membership_version=1,
        source="s",
        term=1,
    )
    assert _synthesize_delta_for_event(invisible) is None

    visible = MembershipEvent(
        seq_no=2,
        room_id="r",
        user_id="u",
        event_type=EventType.JOIN_ACCEPTED,
        timestamp=0.0,
        membership_version=2,
        source="s",
        term=1,
    )
    d = _synthesize_delta_for_event(visible)
    assert d is not None and d.type == "joined" and d.user_id == "u"


# ─── Backfill timeout sweep ─────────────────────────────────────────────


def test_backfill_timeout_sweep_marks_stuck_member_disconnected(tmp_path, monkeypatch):
    c = MembershipCoordinator("room-1", storage_dir=str(tmp_path))
    c.BACKFILL_TIMEOUT_S = 0.1  # speed up the test

    c.handle_join("alice", "Alice")
    c.handle_start_backfill("alice")
    assert c.get_snapshot().members["alice"].state == MemberState.BACKFILLING

    # Simulate clock advance past the timeout. The sweep reads time.time()
    # in coordinator; we monkeypatch it to a future moment.
    base = c.get_snapshot().members["alice"].joined_at
    monkeypatch.setattr(
        "peer_discovery.membership_integration.coordinator.time.time",
        lambda: base + 5.0,
    )

    c.tick()

    assert c.get_snapshot().members["alice"].state == MemberState.DISCONNECTED


def test_backfill_timeout_does_not_fire_within_window(tmp_path, monkeypatch):
    c = MembershipCoordinator("room-1", storage_dir=str(tmp_path))
    c.BACKFILL_TIMEOUT_S = 60.0

    c.handle_join("alice", "Alice")
    c.handle_start_backfill("alice")
    base = c.get_snapshot().members["alice"].joined_at
    monkeypatch.setattr(
        "peer_discovery.membership_integration.coordinator.time.time",
        lambda: base + 1.0,
    )
    c.tick()
    assert c.get_snapshot().members["alice"].state == MemberState.BACKFILLING


def test_backfill_timeout_does_not_affect_active_members(tmp_path, monkeypatch):
    c = MembershipCoordinator("room-1", storage_dir=str(tmp_path))
    c.BACKFILL_TIMEOUT_S = 0.1

    c.handle_join("alice", "Alice")
    c.handle_start_backfill("alice")
    c.handle_complete_backfill("alice")  # → ACTIVE
    assert c.get_snapshot().members["alice"].state == MemberState.ACTIVE

    base = c.get_snapshot().members["alice"].joined_at
    monkeypatch.setattr(
        "peer_discovery.membership_integration.coordinator.time.time",
        lambda: base + 5.0,
    )
    c.tick()
    assert c.get_snapshot().members["alice"].state == MemberState.ACTIVE


def test_backfill_timeout_notifies_subscribers(tmp_path, monkeypatch):
    c = MembershipCoordinator("room-1", storage_dir=str(tmp_path))
    c.BACKFILL_TIMEOUT_S = 0.1

    captured: list[str] = []
    c.subscribe(lambda e, d: captured.append(d.type), from_version=0)

    c.handle_join("alice", "Alice")
    c.handle_start_backfill("alice")

    base = c.get_snapshot().members["alice"].joined_at
    monkeypatch.setattr(
        "peer_discovery.membership_integration.coordinator.time.time",
        lambda: base + 5.0,
    )
    c.tick()

    assert "disconnected" in captured

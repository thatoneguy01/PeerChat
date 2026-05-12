from peer_discovery.membership.durability import DurabilityManager
from peer_discovery.membership.event_log import MembershipEventLog
from peer_discovery.membership.models import EventType, MemberState
from peer_discovery.membership.snapshot import MembershipSnapshot


# Realistic interleaved sequence for two users
_SCENARIO: list[tuple[EventType, str, str]] = [
    (EventType.JOIN_ACCEPTED, "alice", "Alice"),
    (EventType.JOIN_ACCEPTED, "bob", "Bob"),
    (EventType.HISTORY_BACKFILL_STARTED, "alice", ""),
    (EventType.HISTORY_BACKFILL_STARTED, "bob", ""),
    (EventType.HISTORY_BACKFILL_COMPLETE, "alice", ""),
    (EventType.HEARTBEAT, "alice", ""),
    (EventType.HISTORY_BACKFILL_COMPLETE, "bob", ""),
    (EventType.HEARTBEAT, "bob", ""),
    (EventType.DISCONNECT_SUSPECTED, "alice", ""),
    (EventType.RECONNECTED, "alice", ""),
    (EventType.LEAVE_REQUESTED, "bob", ""),
    (EventType.LEAVE_CONFIRMED, "bob", ""),
]


def _run(log, snap, events):
    for et, uid, dname in events:
        e = log.append(et, uid, "coord", term=1, display_name=dname)
        snap.apply_event(e)


def test_full_pipeline_with_recovery(tmp_path):
    dmgr = DurabilityManager(str(tmp_path), snapshot_interval=5)
    log = MembershipEventLog("room-1")
    snap = MembershipSnapshot("room-1")

    # First half — will trigger a checkpoint at seq 5
    for et, uid, dname in _SCENARIO[:5]:
        e = log.append(et, uid, "coord", term=1, display_name=dname)
        snap.apply_event(e)
        dmgr.maybe_checkpoint(log, snap)

    # Continue appending past the checkpoint
    for et, uid, dname in _SCENARIO[5:]:
        e = log.append(et, uid, "coord", term=1, display_name=dname)
        snap.apply_event(e)
        dmgr.maybe_checkpoint(log, snap)

    # Simulate graceful shutdown: force a final checkpoint so uncheckpointed
    # events past the last interval boundary are persisted.
    dmgr.force_checkpoint(log, snap)

    expected_snapshot = snap.serialize()
    expected_latest = log.get_latest_seq_no()
    expected_term = log.get_current_term()

    # Simulate restart: drop in-memory state, recover from disk
    del log
    del snap

    rec = dmgr.recover("room-1")
    assert rec is not None
    rlog, rsnap = rec

    # The recovered state should be identical to what we had before "crash"
    assert rsnap.serialize() == expected_snapshot
    assert rlog.get_latest_seq_no() == expected_latest
    assert rlog.get_current_term() == expected_term
    assert rsnap.get_member("alice").state == MemberState.ACTIVE
    assert rsnap.get_member("bob").state == MemberState.LEFT


def test_determinism_two_independent_runs():
    def build():
        log = MembershipEventLog("room-1")
        snap = MembershipSnapshot("room-1")
        for et, uid, dname in _SCENARIO:
            e = log.append(et, uid, "coord", term=1, display_name=dname)
            snap.apply_event(e)
        # Strip timestamps from log (they wall-clock differ) before comparing —
        # the snapshot itself does not include them in a way that drifts here
        # because it uses event.timestamp, not time.time().
        return snap.serialize()

    a = build()
    b = build()
    # Timestamps come from event.timestamp which is set by log.append() at the
    # moment of writing, so direct equality of serialized snapshots may include
    # timestamp drift. Compare the structural pieces only.
    assert {
        k: v for k, v in a.items() if k != "members"
    } == {k: v for k, v in b.items() if k != "members"}

    for uid in a["members"]:
        am = a["members"][uid]
        bm = b["members"][uid]
        # All non-timestamp fields must match exactly
        for field in ("user_id", "display_name", "state", "membership_version"):
            assert am[field] == bm[field]

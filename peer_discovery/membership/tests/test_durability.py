import json
import os

from peer_discovery.membership.durability import DurabilityManager
from peer_discovery.membership.event_log import MembershipEventLog
from peer_discovery.membership.models import EventType, MemberState
from peer_discovery.membership.snapshot import MembershipSnapshot


def _append_and_apply(log, snap, event_type, user_id, term=1, display_name=""):
    e = log.append(event_type, user_id, "coord", term=term, display_name=display_name)
    snap.apply_event(e)
    return e


def test_recover_returns_none_on_fresh_start(tmp_path):
    d = DurabilityManager(str(tmp_path), snapshot_interval=5)
    assert d.recover("room-1") is None


def test_checkpoint_and_recover(tmp_path):
    d = DurabilityManager(str(tmp_path), snapshot_interval=5)
    log = MembershipEventLog("room-1")
    snap = MembershipSnapshot("room-1")

    # 5 events total triggers a checkpoint at the 5th
    _append_and_apply(log, snap, EventType.JOIN_ACCEPTED, "alice", display_name="Alice")
    _append_and_apply(log, snap, EventType.HISTORY_BACKFILL_STARTED, "alice")
    _append_and_apply(log, snap, EventType.HISTORY_BACKFILL_COMPLETE, "alice")
    _append_and_apply(log, snap, EventType.JOIN_ACCEPTED, "bob", display_name="Bob")
    _append_and_apply(log, snap, EventType.HISTORY_BACKFILL_STARTED, "bob")

    wrote = d.maybe_checkpoint(log, snap)
    assert wrote is True

    rec = d.recover("room-1")
    assert rec is not None
    rlog, rsnap = rec
    assert rlog.get_latest_seq_no() == log.get_latest_seq_no()
    assert rlog.get_current_term() == log.get_current_term()
    assert rsnap.serialize() == snap.serialize()
    assert rsnap.get_member("alice").state == MemberState.ACTIVE
    assert rsnap.get_member("bob").state == MemberState.BACKFILLING


def test_recovery_falls_back_to_older_when_newest_corrupt(tmp_path):
    d = DurabilityManager(str(tmp_path), snapshot_interval=5)
    log = MembershipEventLog("room-1")
    snap = MembershipSnapshot("room-1")
    for i in range(5):
        _append_and_apply(log, snap, EventType.JOIN_ACCEPTED, f"u{i}")
    d.force_checkpoint(log, snap)

    # Take another checkpoint at seq 10
    for i in range(5, 10):
        _append_and_apply(log, snap, EventType.JOIN_ACCEPTED, f"u{i}")
    d.force_checkpoint(log, snap)

    # Corrupt the newest checkpoint file
    files = sorted(os.listdir(str(tmp_path)))
    newest = [f for f in files if f.endswith(".json")]
    newest.sort(
        key=lambda n: int(n.removeprefix("checkpoint_room-1_").removesuffix(".json")),
        reverse=True,
    )
    with open(tmp_path / newest[0], "w") as f:
        f.write("not json {{{")

    rec = d.recover("room-1")
    assert rec is not None
    _, rsnap = rec
    # Older checkpoint had 5 members
    assert len(rsnap.get_snapshot().members) == 5


def test_prune_keeps_last_two(tmp_path):
    d = DurabilityManager(str(tmp_path), snapshot_interval=5)
    log = MembershipEventLog("room-1")
    snap = MembershipSnapshot("room-1")

    for ckpt_num in range(3):
        for _ in range(5):
            _append_and_apply(
                log, snap, EventType.JOIN_ACCEPTED, f"u{log.get_latest_seq_no()}"
            )
        assert d.maybe_checkpoint(log, snap) is True

    files = [f for f in os.listdir(str(tmp_path)) if f.endswith(".json")]
    assert len(files) == 2


def test_no_partial_files_left_behind(tmp_path):
    d = DurabilityManager(str(tmp_path), snapshot_interval=5)
    log = MembershipEventLog("room-1")
    snap = MembershipSnapshot("room-1")
    for _ in range(5):
        _append_and_apply(log, snap, EventType.JOIN_ACCEPTED, f"u{log.get_latest_seq_no()}")
    d.maybe_checkpoint(log, snap)

    leftovers = [f for f in os.listdir(str(tmp_path)) if f.endswith(".tmp")]
    assert leftovers == []

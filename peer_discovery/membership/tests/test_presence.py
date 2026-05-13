from peer_discovery.membership.presence import (
    PresenceManager,
    PresenceState,
)


class FakeClock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def make_pm(clock=None, **kw):
    callbacks: list[tuple[str, str]] = []
    pm = PresenceManager(
        on_state_change=lambda uid, ct: callbacks.append((uid, ct)),
        time_fn=clock,
        heartbeat_interval_s=kw.get("heartbeat_interval_s", 5.0),
        suspect_after_missed=kw.get("suspect_after_missed", 3),
        dead_after_suspect_s=kw.get("dead_after_suspect_s", 15.0),
    )
    return pm, callbacks


def test_new_member_starts_alive():
    clock = FakeClock()
    pm, _ = make_pm(clock=clock)
    pm.register_member("alice")
    entry = pm.get_member_presence("alice")
    assert entry is not None
    assert entry.state == PresenceState.ALIVE
    assert entry.last_heartbeat == 1000.0


def test_heartbeat_updates_timestamp():
    clock = FakeClock()
    pm, _ = make_pm(clock=clock)
    pm.register_member("alice")
    clock.advance(2.0)
    pm.record_heartbeat("alice")
    assert pm.get_member_presence("alice").last_heartbeat == 1002.0


def test_missed_heartbeats_trigger_suspicion():
    clock = FakeClock()
    pm, cbs = make_pm(clock=clock)
    pm.register_member("alice")
    # threshold = 5 * 3 = 15s. Advance past it.
    clock.advance(16.0)
    pm.check_liveness()
    assert cbs == [("alice", "suspected")]
    assert pm.get_member_presence("alice").state == PresenceState.SUSPECTED


def test_no_suspicion_before_threshold():
    clock = FakeClock()
    pm, cbs = make_pm(clock=clock)
    pm.register_member("alice")
    clock.advance(10.0)
    pm.check_liveness()
    assert cbs == []
    assert pm.get_member_presence("alice").state == PresenceState.ALIVE


def test_suspected_member_recovers_on_heartbeat():
    clock = FakeClock()
    pm, cbs = make_pm(clock=clock)
    pm.register_member("alice")
    clock.advance(16.0)
    pm.check_liveness()  # → suspected
    pm.record_heartbeat("alice")
    assert cbs == [("alice", "suspected"), ("alice", "reconnected")]
    assert pm.get_member_presence("alice").state == PresenceState.ALIVE
    assert pm.get_member_presence("alice").suspected_at is None


def test_grace_period_expiry_triggers_timeout():
    clock = FakeClock()
    pm, cbs = make_pm(clock=clock)
    pm.register_member("alice")
    clock.advance(16.0)
    pm.check_liveness()  # suspected at t=1016
    clock.advance(16.0)  # 16s after suspected_at > 15s grace
    pm.check_liveness()
    assert cbs == [("alice", "suspected"), ("alice", "timeout")]
    assert pm.get_member_presence("alice").state == PresenceState.DEAD


def test_heartbeat_for_unknown_user_is_noop():
    pm, cbs = make_pm()
    pm.record_heartbeat("ghost")
    assert cbs == []


def test_unregister_removes_tracking():
    clock = FakeClock()
    pm, cbs = make_pm(clock=clock)
    pm.register_member("alice")
    pm.unregister_member("alice")
    clock.advance(100.0)
    pm.check_liveness()
    assert cbs == []
    assert pm.get_member_presence("alice") is None


def test_unregister_unknown_user_no_crash():
    pm, _ = make_pm()
    pm.unregister_member("nobody")  # should not raise


def test_multiple_suspicion_cycles():
    clock = FakeClock()
    pm, cbs = make_pm(clock=clock)
    pm.register_member("alice")

    # Cycle 1
    clock.advance(16.0)
    pm.check_liveness()
    pm.record_heartbeat("alice")  # back to ALIVE

    # Cycle 2
    clock.advance(16.0)
    pm.check_liveness()
    pm.record_heartbeat("alice")

    assert cbs == [
        ("alice", "suspected"),
        ("alice", "reconnected"),
        ("alice", "suspected"),
        ("alice", "reconnected"),
    ]


def test_check_liveness_empty_noop():
    pm, cbs = make_pm()
    pm.check_liveness()
    assert cbs == []


def test_configurable_thresholds():
    clock = FakeClock()
    pm, cbs = make_pm(
        clock=clock,
        heartbeat_interval_s=1.0,
        suspect_after_missed=2,
        dead_after_suspect_s=3.0,
    )
    pm.register_member("alice")
    clock.advance(3.0)  # > 1.0 * 2 = 2.0
    pm.check_liveness()
    assert cbs == [("alice", "suspected")]
    clock.advance(4.0)  # > 3.0 grace
    pm.check_liveness()
    assert cbs[-1] == ("alice", "timeout")


def test_tracked_count():
    pm, _ = make_pm()
    pm.register_member("a")
    pm.register_member("b")
    pm.register_member("c")
    assert pm.tracked_count == 3
    pm.unregister_member("b")
    assert pm.tracked_count == 2


def test_get_all_entries_returns_copy():
    pm, _ = make_pm()
    pm.register_member("alice")
    snap = pm.get_all_entries()
    snap.pop("alice")
    assert pm.get_member_presence("alice") is not None


def test_returned_entries_do_not_mutate_internal_state():
    pm, _ = make_pm()
    pm.register_member("alice")

    entry = pm.get_member_presence("alice")
    entry.state = PresenceState.DEAD

    entries = pm.get_all_entries()
    entries["alice"].state = PresenceState.DEAD

    assert pm.get_member_presence("alice").state == PresenceState.ALIVE

from peer_discovery.membership.models import (
    EventType,
    JoinResult,
    MemberInfo,
    MemberState,
    MembershipDelta,
    MembershipEvent,
    MembershipSnapshotData,
    SubscriptionHandle,
    ValidationResult,
)


def make_event(**overrides) -> MembershipEvent:
    base = dict(
        seq_no=1,
        room_id="room-1",
        user_id="alice",
        event_type=EventType.JOIN_ACCEPTED,
        timestamp=123.0,
        membership_version=1,
        source="coordinator",
        term=1,
        trace_id="trace-1",
        display_name="Alice",
    )
    base.update(overrides)
    return MembershipEvent(**base)


def test_membership_event_round_trip():
    e = make_event()
    assert MembershipEvent.from_dict(e.to_dict()) == e


def test_membership_event_round_trip_no_optionals():
    e = make_event(trace_id=None, display_name="")
    assert MembershipEvent.from_dict(e.to_dict()) == e


def test_membership_event_is_hashable():
    e1 = make_event()
    e2 = make_event()
    assert hash(e1) == hash(e2)
    assert {e1, e2} == {e1}


def test_member_info_round_trip():
    m = MemberInfo(
        user_id="bob",
        display_name="Bob",
        state=MemberState.ACTIVE,
        joined_at=10.0,
        last_heartbeat=20.0,
        membership_version=5,
    )
    assert MemberInfo.from_dict(m.to_dict()) == m


def test_join_result_construction():
    r = JoinResult(accepted=True, seq_no=42, membership_version=42, active_members=[])
    assert r.reason is None
    r2 = JoinResult(accepted=False, seq_no=-1, membership_version=0, active_members=[], reason="banned")
    assert r2.reason == "banned"


def test_membership_delta_optional_event():
    d = MembershipDelta(type="joined", user_id="alice")
    assert d.event is None


def test_validation_result():
    v = ValidationResult(accepted=True)
    assert v.accepted is True and v.reason is None


def test_subscription_handle():
    h = SubscriptionHandle(id="abc")
    assert h.id == "abc"


def test_snapshot_data_immutable_dataclass():
    data = MembershipSnapshotData(
        room_id="room-1", version=1, members={}, active_count=0, as_of_seq_no=0
    )
    assert data.room_id == "room-1"


def test_event_type_enum_values():
    assert EventType("JOIN_ACCEPTED") is EventType.JOIN_ACCEPTED
    assert MemberState("ACTIVE") is MemberState.ACTIVE

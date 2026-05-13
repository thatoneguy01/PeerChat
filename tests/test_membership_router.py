"""
MembershipRouter regression tests.

Uses hand-rolled fakes that mimic the shape of Peer Discovery's
MembershipService (snapshot + subscribe_membership_events). Does NOT
import from peer_discovery/ so these tests stay lightweight and pass
on any Python version our module supports.
"""

from dataclasses import dataclass
from typing import Callable

from distribution.membership_router import MembershipRouter


@dataclass
class FakeState:
    name: str


@dataclass
class FakeMember:
    state: FakeState


@dataclass
class FakeSnapshot:
    members: dict
    version: int


class FakeEventType:
    def __init__(self, name: str) -> None:
        self.name = name


@dataclass
class FakeEvent:
    event_type: FakeEventType
    user_id: str


class FakeService:
    def __init__(self, snapshot: FakeSnapshot) -> None:
        self._snapshot = snapshot
        self._callback: Callable = None

    def get_membership_snapshot(self) -> FakeSnapshot:
        return self._snapshot

    def subscribe_membership_events(self, callback, from_version: int = 0):
        self._callback = callback
        return object()

    def fire(self, event_name: str, user_id: str) -> None:
        self._callback(FakeEvent(FakeEventType(event_name), user_id))


def _member(state_name: str) -> FakeMember:
    return FakeMember(state=FakeState(state_name))


def test_init_peer_in_joining_state_is_held_not_invisible():
    """Regression for Himanshu's catch: a peer in JOINING state at boot must
    land in _hold, so the later HISTORY_BACKFILL_COMPLETE event can promote it."""
    snapshot = FakeSnapshot(
        members={
            "127.0.0.1:5001": _member("JOINING"),
            "127.0.0.1:5002": _member("ACTIVE"),
        },
        version=0,
    )
    service = FakeService(snapshot)
    router = MembershipRouter(service, self_address="127.0.0.1:5000")

    # JOINING peer is held, not in active
    assert router.get_peers() == [("127.0.0.1", 5002)]
    assert "127.0.0.1:5001" in router._hold

    # Backfill completes → peer becomes routable
    service.fire("HISTORY_BACKFILL_COMPLETE", "127.0.0.1:5001")
    peers = sorted(router.get_peers())
    assert peers == [("127.0.0.1", 5001), ("127.0.0.1", 5002)]


def test_init_peer_in_backfilling_state_is_held():
    snapshot = FakeSnapshot(
        members={"127.0.0.1:5001": _member("BACKFILLING")},
        version=0,
    )
    router = MembershipRouter(FakeService(snapshot), self_address="127.0.0.1:5000")
    assert router.get_peers() == []
    assert "127.0.0.1:5001" in router._hold


def test_init_skips_tombstoned_members():
    """LEFT / DISCONNECTED / LEAVING members may appear in snapshot but must not route."""
    snapshot = FakeSnapshot(
        members={
            "127.0.0.1:5001": _member("LEFT"),
            "127.0.0.1:5002": _member("DISCONNECTED"),
            "127.0.0.1:5003": _member("LEAVING"),
            "127.0.0.1:5004": _member("ACTIVE"),
        },
        version=0,
    )
    router = MembershipRouter(FakeService(snapshot), self_address="127.0.0.1:5000")
    assert router.get_peers() == [("127.0.0.1", 5004)]


def test_init_excludes_self():
    snapshot = FakeSnapshot(
        members={
            "127.0.0.1:5000": _member("ACTIVE"),        # self
            "127.0.0.1:5001": _member("ACTIVE"),
        },
        version=0,
    )
    router = MembershipRouter(FakeService(snapshot), self_address="127.0.0.1:5000")
    assert router.get_peers() == [("127.0.0.1", 5001)]

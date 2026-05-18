"""Tests for HeartbeatManager.

After the consolidation, inbound heartbeats are handled by
DiscoveryNode._handle_heartbeat (called from handle_message). These tests
hit that path directly with a synthesized envelope rather than the deleted
legacy handle_incoming_heartbeat function.
"""
import time

from peer_discovery.membership.models import EventType, MembershipEvent
from peer_discovery.network.config import DiscoveryConfig
from peer_discovery.network.discovery_node import DiscoveryNode
from peer_discovery.network.tests._helpers import FakeBroadcastNode


def _make_node(tmp_path, address="127.0.0.1:5678"):
    fake = FakeBroadcastNode(address)
    config = DiscoveryConfig(
        advertise_address=address,
        public_key_override=b"PEM",
        heartbeat_interval=0.1,
        tick_interval=0.1,
    )
    node = DiscoveryNode("room-1", config, str(tmp_path), broadcast_node=fake)
    return node, fake


def test_heartbeat_manager_lifecycle(tmp_path):
    node, _ = _make_node(tmp_path)

    # Plant a known peer so heartbeats have somewhere to go.
    event = MembershipEvent(
        seq_no=1, room_id="room-1", user_id="127.0.0.1:5679",
        event_type=EventType.JOIN_ACCEPTED, timestamp=time.time(),
        membership_version=1, source="remote", term=1,
    )
    node.service.apply_remote_event(event)

    node.start()
    try:
        time.sleep(0.3)
        assert node._heartbeat_manager._running
    finally:
        node.stop()


def test_handle_heartbeat_known(tmp_path):
    """A heartbeat from a known peer updates their last_heartbeat."""
    node, _ = _make_node(tmp_path)

    event = MembershipEvent(
        seq_no=1, room_id="room-1", user_id="127.0.0.1:5679",
        event_type=EventType.JOIN_ACCEPTED, timestamp=time.time(),
        membership_version=1, source="remote", term=1,
    )
    node.service.apply_remote_event(event)

    # Hand the heartbeat to the new dispatcher directly.
    result = node._handle_heartbeat("127.0.0.1:5679")
    assert result == {"handled": True}

    snap = node.service.get_membership_snapshot()
    assert snap.members["127.0.0.1:5679"].last_heartbeat >= event.timestamp


def test_handle_heartbeat_unknown(tmp_path):
    """A heartbeat from an unknown peer is dropped silently — no membership change."""
    node, _ = _make_node(tmp_path)

    result = node._handle_heartbeat("127.0.0.1:9999")
    assert result == {"handled": True}

    snap = node.service.get_membership_snapshot()
    assert "127.0.0.1:9999" not in snap.members

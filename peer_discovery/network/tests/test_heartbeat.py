"""Tests for HeartbeatManager."""
import time

from peer_discovery.membership.models import EventType, MembershipEvent
from peer_discovery.network.config import DiscoveryConfig
from peer_discovery.network.discovery_node import DiscoveryNode


def test_heartbeat_manager_lifecycle(tmp_path):
    config = DiscoveryConfig(
        advertise_address="node_a",
        listen_port=0,
        heartbeat_interval=0.1,
        tick_interval=0.1
    )
    node = DiscoveryNode("room-1", config, str(tmp_path))
    
    # Fake member so heartbeat has somewhere to go
    event = MembershipEvent(
        seq_no=1, room_id="room-1", user_id="node_b",
        event_type=EventType.JOIN_ACCEPTED, timestamp=time.time(),
        membership_version=1, source="remote", term=1
    )
    node.service.apply_remote_event(event)
    
    node.start()
    try:
        time.sleep(0.3)
        # Should not crash
        assert node._heartbeat_manager._running
    finally:
        node.stop()


def test_handle_incoming_heartbeat_known(tmp_path):
    config = DiscoveryConfig(
        advertise_address="node_a",
        listen_port=0
    )
    node = DiscoveryNode("room-1", config, str(tmp_path))
    
    event = MembershipEvent(
        seq_no=1, room_id="room-1", user_id="node_b",
        event_type=EventType.JOIN_ACCEPTED, timestamp=time.time(),
        membership_version=1, source="remote", term=1
    )
    node.service.apply_remote_event(event)
    
    # Fake heartbeat
    from peer_discovery.network.protocol import MessageType, NetworkMessage
    msg = NetworkMessage(
        message_type=MessageType.HEARTBEAT,
        sender_id="node_b",
        payload={}
    )
    
    from peer_discovery.network.heartbeat import handle_incoming_heartbeat
    handle_incoming_heartbeat(node, msg)
    
    snap = node.service.get_membership_snapshot()
    assert snap.members["node_b"].last_heartbeat >= event.timestamp


def test_handle_incoming_heartbeat_unknown(tmp_path):
    config = DiscoveryConfig(
        advertise_address="node_a",
        listen_port=0
    )
    node = DiscoveryNode("room-1", config, str(tmp_path))
    
    from peer_discovery.network.protocol import MessageType, NetworkMessage
    msg = NetworkMessage(
        message_type=MessageType.HEARTBEAT,
        sender_id="unknown_peer",
        payload={}
    )
    
    from peer_discovery.network.heartbeat import handle_incoming_heartbeat
    handle_incoming_heartbeat(node, msg)
    
    snap = node.service.get_membership_snapshot()
    assert "unknown_peer" not in snap.members

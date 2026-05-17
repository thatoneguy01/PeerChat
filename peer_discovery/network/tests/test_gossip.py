"""Tests for GossipDispatcher."""
from peer_discovery.membership.models import EventType, MembershipEvent
from peer_discovery.network.config import DiscoveryConfig
from peer_discovery.network.discovery_node import DiscoveryNode


def test_gossip_dispatcher_dedup(tmp_path):
    config = DiscoveryConfig(
        advertise_address="node_a",
        listen_port=0
    )
    node = DiscoveryNode("room-1", config, str(tmp_path))
    node.start()
    
    try:
        dispatcher = node._gossip_dispatcher
        
        event = MembershipEvent(
            seq_no=1,
            room_id="room-1",
            user_id="bob",
            event_type=EventType.JOIN_ACCEPTED,
            timestamp=1.0,
            membership_version=1,
            source="remote",
            term=1,
            originator="node_b"
        )
        
        # Test dedup logic directly
        event_id = "node_b:1:JOIN_ACCEPTED:bob"
        
        assert not dispatcher._mark_seen(event_id)
        assert dispatcher._mark_seen(event_id)
        
    finally:
        node.stop()

"""Tests for GossipDispatcher."""
from peer_discovery.membership.models import EventType, MembershipEvent
from peer_discovery.network.config import DiscoveryConfig
from peer_discovery.network.discovery_node import DiscoveryNode
from peer_discovery.network.tests._helpers import FakeBroadcastNode


def test_gossip_dispatcher_dedup(tmp_path):
    fake = FakeBroadcastNode("127.0.0.1:5678")
    config = DiscoveryConfig(
        advertise_address="127.0.0.1:5678",
        public_key_override=b"PEM",
    )
    node = DiscoveryNode("room-1", config, str(tmp_path), broadcast_node=fake)

    try:
        node.start()
        dispatcher = node._gossip_dispatcher

        event_id = "node_b:1:JOIN_ACCEPTED:bob"
        assert not dispatcher._mark_seen(event_id)
        assert dispatcher._mark_seen(event_id)
    finally:
        node.stop()

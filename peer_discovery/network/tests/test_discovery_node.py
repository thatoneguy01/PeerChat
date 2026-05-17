"""Tests for DiscoveryNode skeleton."""
from peer_discovery.network.config import DiscoveryConfig
from peer_discovery.network.discovery_node import DiscoveryNode


def test_discovery_node_lifecycle(tmp_path):
    config = DiscoveryConfig(
        advertise_address="127.0.0.1:0",
        listen_port=0,
    )
    
    node = DiscoveryNode(
        room_id="test-room",
        config=config,
        storage_dir=str(tmp_path)
    )
    
    try:
        node.start()
        assert node._running
        
        # Verify transport is listening
        host, port = node.listener.bound_address
        assert port > 0
    finally:
        node.stop()
        assert not node._running

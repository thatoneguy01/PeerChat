"""Tests for DiscoveryNode lifecycle with a FakeBroadcastNode."""
from peer_discovery.network.config import DiscoveryConfig
from peer_discovery.network.discovery_node import DiscoveryNode
from peer_discovery.network.tests._helpers import FakeBroadcastNode


def test_discovery_node_lifecycle(tmp_path):
    fake = FakeBroadcastNode("127.0.0.1:5678")
    config = DiscoveryConfig(
        advertise_address="127.0.0.1:5678",
        public_key_override=b"PEM-FAKE-PUBKEY",
    )

    node = DiscoveryNode(
        room_id="test-room",
        config=config,
        storage_dir=str(tmp_path),
        broadcast_node=fake,
    )

    try:
        node.start(display_name="solo")
        assert node._running
        # Seed-mode bootstrap: should have joined itself.
        snap = node.service.get_membership_snapshot()
        assert "127.0.0.1:5678" in snap.members
    finally:
        node.stop()
        assert not node._running

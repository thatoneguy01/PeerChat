from distribution.broadcast_node import BroadcastNode
from distribution.message import Message
from distribution.peer_registry import InMemoryRegistry
import socket, time
from peer_discovery.membership_integration.service import MembershipService
from peer_discovery.network.config import DiscoveryConfig
from peer_discovery.network.discovery_node import DiscoveryNode
from peer_discovery.membership.models import EventType


def main():
    config = DiscoveryConfig(advertise_address=f"73.223.1.181:8102", listen_port=8102, bootstrap_peers=[f"10.0.0.165:8001"])
    node = DiscoveryNode(room_id="default", config=config, storage_dir="../storage")
    discover_node = node
    discover_service = discover_node.service
    discover_node.start(display_name="TestUser")
    mebership_snapshot = discover_node.service.get_membership_snapshot()


if __name__ == "__main__":
    main()
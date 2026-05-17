"""The main DiscoveryNode integrating the network and membership layers."""
import logging
from typing import Any

from peer_discovery.membership_integration.service import MembershipService
from peer_discovery.network.config import DiscoveryConfig
from peer_discovery.network.crypto_provider import NullCryptoProvider, RSACryptoProvider
from peer_discovery.network.keys import generate_or_load_keypair
from peer_discovery.network.protocol import NetworkMessage
from peer_discovery.network.transport import TCPClient, TCPListener

logger = logging.getLogger(__name__)


class DiscoveryNode:
    """Coordinates P2P networking with the underlying MembershipService."""

    def __init__(self, room_id: str, config: DiscoveryConfig, storage_dir: str):
        self.config = config
        self.room_id = room_id
        
        # 1. Initialize membership service
        self.service = MembershipService(room_id, storage_dir)
        
        # 2. Initialize crypto
        if config.enable_crypto:
            priv_key = generate_or_load_keypair(config.key_dir)
            self.crypto = RSACryptoProvider(priv_key)
        else:
            self.crypto = NullCryptoProvider()
            
        # 3. Initialize transport
        self.client = TCPClient()
        self.listener = TCPListener(
            host="0.0.0.0",
            port=config.listen_port,
            handler=self._handle_network_message
        )
        self._running = False
        
        # To be implemented in later phases
        from peer_discovery.network.gossip import GossipDispatcher
        self._gossip_dispatcher = GossipDispatcher(self)
        
        from peer_discovery.network.heartbeat import HeartbeatManager
        self._heartbeat_manager = HeartbeatManager(self)

    @property
    def advertise_address(self) -> str:
        return self.config.advertise_address

    def start(self, display_name: str = "Node") -> None:
        """Start the node and connect to the network."""
        logger.info("Starting DiscoveryNode for room %s on port %d", self.room_id, self.config.listen_port)
        self.listener.start()
        self._running = True
        
        # Phase 7: Bootstrap
        from peer_discovery.network.bootstrap import attempt_bootstrap
        success = attempt_bootstrap(self, display_name)
        if not success:
            logger.warning("Node started but failed to join the network.")
        
        # Phase 8: Gossip
        self.service.subscribe_membership_events(self._gossip_dispatcher.dispatch)
        
        # Phase 9: Heartbeat and tick
        self._heartbeat_manager.start()

    def stop(self) -> None:
        """Stop the node and cleanly leave the network."""
        logger.info("Stopping DiscoveryNode for room %s", self.room_id)
        self._running = False
        self.listener.stop()
        
        # Stop background tasks (Phase 9)
        self._heartbeat_manager.stop()

    def _handle_network_message(self, source_ip: str, msg: NetworkMessage) -> NetworkMessage | None:
        """Route incoming network messages to appropriate handlers."""
        logger.debug("Received %s from %s", msg.message_type.value, source_ip)
        
        from peer_discovery.network.protocol import MessageType
        
        if msg.message_type == MessageType.JOIN_REQUEST:
            from peer_discovery.network.bootstrap import handle_join_request
            return handle_join_request(self, source_ip, msg)
            
        if msg.message_type == MessageType.EVENT_BROADCAST:
            self._gossip_dispatcher.handle_incoming_gossip(source_ip, msg)
            return None
            
        if msg.message_type == MessageType.HEARTBEAT:
            from peer_discovery.network.heartbeat import handle_incoming_heartbeat
            handle_incoming_heartbeat(self, msg)
            return None
            
        # Dispatch to specific handlers based on message type
        # To be implemented in phases 9
        return None

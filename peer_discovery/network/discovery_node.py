"""The main DiscoveryNode integrating the network and membership layers."""
import logging
from typing import Any

from peer_discovery.membership_integration.service import MembershipService
from peer_discovery.network.config import DiscoveryConfig
from peer_discovery.network.crypto_provider import NullCryptoProvider, RSACryptoProvider
from peer_discovery.network.keys import generate_or_load_keypair
from peer_discovery.network.protocol import NetworkMessage
from peer_discovery.network.transport import WebSocketClient, WebSocketListener

logger = logging.getLogger(__name__)


class DiscoveryNode:
    """Coordinates P2P networking with the underlying MembershipService."""

    def __init__(self, room_id: str, config: DiscoveryConfig, storage_dir: str):
        self.config = config
        self.room_id = room_id

        logger.info(
            "init room=%s advertise=%s listen_port=%d crypto=%s storage_dir=%s "
            "bootstrap_peers=%s bootstrap_timeout=%.1fs",
            room_id, config.advertise_address, config.listen_port,
            "RSA" if config.enable_crypto else "Null",
            storage_dir, config.bootstrap_peers, config.bootstrap_timeout,
        )

        # 1. Initialize membership service
        self.service = MembershipService(room_id, storage_dir)

        # 2. Initialize crypto
        if config.enable_crypto:
            priv_key = generate_or_load_keypair(config.key_dir)
            self.crypto = RSACryptoProvider(priv_key)
            logger.info(
                "crypto_ready provider=RSACryptoProvider pubkey_bytes=%d",
                len(self.crypto.get_public_key_bytes()),
            )
        else:
            self.crypto = NullCryptoProvider()
            logger.info("crypto_ready provider=NullCryptoProvider (testing only)")

        # 3. Initialize transport
        self.client = WebSocketClient(timeout=config.bootstrap_timeout)
        self.listener = WebSocketListener(
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
        logger.info(
            "start room=%s advertise=%s display_name=%s",
            self.room_id, self.config.advertise_address, display_name,
        )

        # If the History team hasn't registered a backfill handler and the
        # config opts in to auto-completion, install a default that drives
        # JOINING → BACKFILLING → ACTIVE locally. Without this, joiners stay
        # in JOINING forever (Distribution holds traffic) until the 30s
        # backfill-timeout sweep kicks them out.
        if self.config.auto_complete_backfill and not self.service.has_history_handler:
            self.service.register_history_handler(self._auto_complete_backfill_handler)
            logger.info("history_handler=auto_complete (no external handler registered)")
        elif self.service.has_history_handler:
            logger.info("history_handler=external (registered by another component)")

        self.listener.start()
        logger.info("listener_started bind=0.0.0.0:%d", self.config.listen_port)
        self._running = True

        # Phase 7: Bootstrap
        from peer_discovery.network.bootstrap import attempt_bootstrap
        success = attempt_bootstrap(self, display_name)
        if success:
            snap = self.service.get_membership_snapshot()
            logger.info(
                "start_complete bootstrap=success members=%d active=%d",
                len(snap.members), snap.active_count,
            )
        else:
            logger.warning(
                "start_complete bootstrap=FAILED — node running but isolated; "
                "check seed reachability"
            )

        # Phase 8: Gossip
        self.service.subscribe_membership_events(self._gossip_dispatcher.dispatch)
        logger.info("gossip_subscribed dispatcher=GossipDispatcher")

        # Phase 9: Heartbeat and tick
        self._heartbeat_manager.start()
        logger.info(
            "heartbeat_started interval=%.1fs tick_interval=%.1fs",
            self.config.heartbeat_interval, self.config.tick_interval,
        )

    def _auto_complete_backfill_handler(self, user_id: str, event: Any) -> None:
        """Default no-op History bridge: immediately promote the joiner from
        JOINING → BACKFILLING → ACTIVE so Distribution will route traffic.
        Used when no History team handler is registered (standalone demos).
        """
        self.service.start_history_backfill(user_id)
        self.service.complete_history_backfill(user_id)

    def stop(self) -> None:
        """Stop the node and cleanly leave the network."""
        logger.info("stop room=%s advertise=%s", self.room_id, self.config.advertise_address)
        self._running = False
        self.listener.stop()

        # Stop background tasks (Phase 9)
        self._heartbeat_manager.stop()
        logger.info("stop_complete room=%s", self.room_id)

    def _handle_network_message(self, source_ip: str, msg: NetworkMessage) -> NetworkMessage | None:
        """Route incoming network messages to appropriate handlers."""
        from peer_discovery.network.protocol import MessageType

        # HEARTBEATs are high-volume; keep at DEBUG. Everything else INFO.
        if msg.message_type == MessageType.HEARTBEAT:
            logger.debug("recv type=%s from=%s sender=%s", msg.message_type.value, source_ip, msg.sender_id)
        else:
            logger.info("recv type=%s from=%s sender=%s", msg.message_type.value, source_ip, msg.sender_id)

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

        logger.warning("recv type=%s UNHANDLED from=%s sender=%s", msg.message_type.value, source_ip, msg.sender_id)
        return None

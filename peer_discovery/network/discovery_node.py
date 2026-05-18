"""The main DiscoveryNode integrating the network and membership layers.

After the Phase-5 consolidation, DiscoveryNode does not own any transport
or crypto code of its own. All wire traffic is routed through Distribution's
``BroadcastNode`` (signed messages on port 5678, fan-out + retry handled by
Distribution). The local node's public key comes from Security's keystore,
injected via ``DiscoveryConfig.public_key_override``.
"""
import logging
import threading
from typing import Any, Optional, TYPE_CHECKING

from peer_discovery.membership_integration.service import MembershipService
from peer_discovery.network.config import DiscoveryConfig
from peer_discovery.network.protocol import (
    SUBTYPE_GOSSIP,
    SUBTYPE_HEARTBEAT,
    SUBTYPE_JOIN_REQUEST,
    SUBTYPE_JOIN_RESPONSE,
    decode_discovery_envelope,
    is_discovery_message,
)

if TYPE_CHECKING:
    from distribution.broadcast_node import BroadcastNode
    from distribution.message import Message

logger = logging.getLogger(__name__)


class DiscoveryNode:
    """Coordinates P2P networking with the underlying MembershipService."""

    def __init__(
        self,
        room_id: str,
        config: DiscoveryConfig,
        storage_dir: str,
        broadcast_node: "Optional[BroadcastNode]" = None,
    ):
        self.config = config
        self.room_id = room_id
        # Distribution's BroadcastNode (port 5678). All discovery traffic —
        # JOIN_REQUEST/JOIN_RESPONSE, gossip events, heartbeats — rides this
        # transport. Mandatory in production; tests can stub this with a
        # FakeBroadcastNode.
        self.broadcast_node = broadcast_node

        # Local node's public key in PEM bytes, sourced externally from
        # Security's keystore (main.py: chat_service.public_key_pem). Used in
        # every outgoing discovery envelope so other peers can register it
        # for chat verify().
        self._public_key_pem: bytes = config.public_key_override or b""
        if not self._public_key_pem:
            logger.warning(
                "no_public_key_pem — caller did not provide one. Other peers "
                "will receive our MemberInfo.public_key as empty bytes, which "
                "breaks Distribution's verify() for chat messages from us."
            )

        # Per-bootstrap-target threading.Event registry. When the joiner
        # sends a JOIN_REQUEST, it registers an Event keyed by the seed's
        # bootstrap_peers entry; the _handle_join_response handler sets the
        # Event when the matching response arrives via Distribution's
        # on_message dispatch. Used by attempt_bootstrap to block with a
        # timeout.
        self._pending_joins: dict[str, threading.Event] = {}
        self._pending_joins_lock = threading.Lock()

        logger.info(
            "init room=%s advertise=%s pubkey_bytes=%d storage_dir=%s "
            "bootstrap_peers=%s bootstrap_timeout=%.1fs",
            room_id, config.advertise_address, len(self._public_key_pem),
            storage_dir, config.bootstrap_peers, config.bootstrap_timeout,
        )

        # 1. Initialize membership service
        self.service = MembershipService(room_id, storage_dir,
                                         local_user_id=config.advertise_address)

        self._running = False

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

        self._running = True

        # Bootstrap: send JOIN_REQUEST via BroadcastNode and wait for response
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

    @property
    def public_key_pem(self) -> bytes:
        """The local node's public key (PEM bytes from Security's keystore)."""
        return self._public_key_pem

    # ------------------------------------------------------------------
    # New (Phase 2): handle inbound discovery messages routed via
    # Distribution's BroadcastNode.on_message → chat_service.message_received.
    # These methods are dormant in Phase 2 (nothing calls handle_message yet).
    # Wired up in Phase 3, exercised in Phase 4.
    # ------------------------------------------------------------------

    def handle_message(self, msg: "Message") -> dict:
        """Entry point for messages that arrived through Distribution's
        transport. Returns ``{"handled": True}`` if this was a discovery
        message we processed, else ``{"handled": False}`` so the caller
        (chat_service.message_received) can fall through to history/chat.
        """
        if not is_discovery_message(getattr(msg, "content", "")):
            return {"handled": False}
        try:
            subtype, sender_pub_pem, payload = decode_discovery_envelope(msg.content)
        except Exception as exc:
            logger.warning(
                "discovery_envelope_malformed sender=%s err=%s",
                getattr(msg, "sender", "<unknown>"), exc,
            )
            return {"handled": False, "reason": "malformed_discovery_envelope"}

        sender_id = msg.sender
        logger.info(
            "discovery_msg_received subtype=%s sender=%s pubkey_bytes=%d",
            subtype, sender_id, len(sender_pub_pem),
        )

        if subtype == SUBTYPE_JOIN_REQUEST:
            return self._handle_join_request(sender_id, sender_pub_pem, payload)
        if subtype == SUBTYPE_JOIN_RESPONSE:
            return self._handle_join_response(sender_id, sender_pub_pem, payload)
        if subtype == SUBTYPE_GOSSIP:
            return self._handle_gossip(sender_id, sender_pub_pem, payload)
        if subtype == SUBTYPE_HEARTBEAT:
            return self._handle_heartbeat(sender_id)
        logger.warning("discovery_msg_unknown_subtype subtype=%s sender=%s", subtype, sender_id)
        return {"handled": False, "reason": f"unknown_subtype:{subtype}"}

    def lazy_register_pubkey(self, msg: "Message") -> None:
        """Pre-verify hook registered on BroadcastNode. Runs BEFORE
        Distribution's signature verification — so if the sender is a brand
        new joiner whose pubkey isn't yet in peer_registry, we plant it from
        the message's own envelope so verify() can succeed (trust-on-
        first-use).

        If the message content is encrypted (new payload encryption layer),
        we decrypt a copy first before trying to parse as a discovery envelope.

        Idempotent: skip if the registry already has a key for this sender.
        Silent failure: any error is swallowed because Distribution catches
        and logs hook exceptions itself.
        """
        if self.broadcast_node is None:
            return
        content = getattr(msg, "content", "")
        logger.info("lazy_register_pubkey: processing message %s, content: %r", msg.id[:8], content[:200])

        # If content is encrypted, decrypt a copy before parsing.
        # Import lazily to avoid circular imports at module load time.
        try:
            from security.payload_encryption import is_encrypted_content, decrypt_payload
            from security.message_integrity import get_private_key_pem
            from dataclasses import replace as dc_replace
            if is_encrypted_content(content):
                private_pem = get_private_key_pem()
                if private_pem:
                    tmp = dc_replace(msg)
                    try:
                        decrypt_payload(tmp, self.broadcast_node.address, private_pem)
                        content = tmp.content
                    except Exception:
                        pass  # decryption failed — fall through, hook will be a no-op
        except ImportError:
            pass  # encryption module not available; use content as-is

        if not is_discovery_message(content):
            logger.warning("lazy_register_pubkey: not discovery message. content prefix: %r", content[:50])
            return
        try:
            _subtype, sender_pub_pem, _payload = decode_discovery_envelope(content)
        except Exception as e:
            logger.warning("lazy_register_pubkey: decode failed: %s", e)
            return
        if not sender_pub_pem:
            logger.warning("lazy_register_pubkey: no sender_pub_pem in envelope")
            return
        sender = getattr(msg, "sender", "")
        try:
            host, port_str = sender.rsplit(":", 1)
            port = int(port_str)
        except ValueError as e:
            logger.warning("lazy_register_pubkey: bad sender format %s", sender)
            return
        registry = self.broadcast_node.peer_registry
        if not hasattr(registry, "get_pub_key") or not hasattr(registry, "add_peer"):
            logger.warning("lazy_register_pubkey: registry missing methods")
            return
        existing = registry.get_pub_key(host, port)
        if existing:
            return  # already registered, idempotent skip
        try:
            registry.add_peer(host, port, sender_pub_pem)
            logger.info(
                "lazy_register_pubkey sender=%s pubkey_bytes=%d",
                sender, len(sender_pub_pem),
            )
        except Exception as exc:
            logger.warning("lazy_register_pubkey_failed sender=%s err=%s", sender, exc)


    # --- subtype handlers ------------------------------------------------

    def _handle_join_request(self, sender_id: str, sender_pub_pem: bytes, payload: dict) -> dict:
        """Seed-side: a new joiner sent a JOIN_REQUEST. Append them to
        membership and send a JOIN_RESPONSE back via the broadcast node.
        """
        display_name = payload.get("display_name", "Unknown")
        logger.info(
            "discovery_join_request sender=%s display_name=%s pubkey_bytes=%d",
            sender_id, display_name, len(sender_pub_pem),
        )

        res = self.service.join_member(
            user_id=sender_id,
            display_name=display_name,
            public_key=sender_pub_pem,
            context={"source_address": sender_id, "public_key": sender_pub_pem},
        )

        if not res.accepted:
            logger.warning(
                "discovery_join_rejected sender=%s reason=%s",
                sender_id, res.reason,
            )
            self._send_join_response(sender_id, accepted=False, reason=res.reason, events=[])
            return {"handled": True}

        snap = self.service.get_membership_snapshot()
        events = self.service._coordinator._log.get_events_since(0)
        events_data = [e.to_dict() for e in events]

        logger.info(
            "discovery_join_accepted sender=%s seq_no=%d members_now=%d events_to_send=%d",
            sender_id, res.seq_no, len(snap.members), len(events_data),
        )

        self._send_join_response(sender_id, accepted=True, reason=None, events=events_data)
        return {"handled": True}

    def _handle_join_response(self, sender_id: str, sender_pub_pem: bytes, payload: dict) -> dict:
        """Joiner-side: seed replied to our JOIN_REQUEST. Apply the snapshot
        and wake any bootstrap thread waiting on this seed.
        """
        accepted = payload.get("accepted", False)
        events_data = payload.get("events", [])

        if not accepted:
            reason = payload.get("reason", "unknown")
            logger.error(
                "discovery_join_response_rejected from=%s reason=%s",
                sender_id, reason,
            )
        else:
            from peer_discovery.membership.models import MembershipEvent
            events = [MembershipEvent.from_dict(d) for d in events_data]
            logger.info(
                "discovery_join_response_applying from=%s events=%d",
                sender_id, len(events),
            )
            try:
                self.service.apply_remote_snapshot(events)
                snap = self.service.get_membership_snapshot()
                logger.info(
                    "discovery_join_response_applied from=%s members_now=%d active_now=%d",
                    sender_id, len(snap.members), snap.active_count,
                )
            except Exception as exc:
                logger.error(
                    "discovery_join_response_apply_failed from=%s err=%s",
                    sender_id, exc,
                )

        # Wake the bootstrap thread waiting on this seed (if any).
        with self._pending_joins_lock:
            pending = self._pending_joins.get(sender_id)
        if pending is not None:
            pending.set()
        return {"handled": True}

    def _handle_gossip(self, sender_id: str, sender_pub_pem: bytes, payload: dict) -> dict:
        """Apply a remote membership event (JOIN_ACCEPTED, LEAVE_CONFIRMED,
        HISTORY_BACKFILL_*, etc.) carried in a gossip envelope.
        """
        from peer_discovery.membership.models import MembershipEvent
        event_data = payload.get("event")
        if not event_data:
            logger.warning("discovery_gossip_no_event sender=%s", sender_id)
            return {"handled": True}
        try:
            event = MembershipEvent.from_dict(event_data)
        except Exception as exc:
            logger.warning("discovery_gossip_parse_failed sender=%s err=%s", sender_id, exc)
            return {"handled": True}

        # Dedup using the same key shape the existing GossipDispatcher uses.
        originator = event.originator or event.user_id
        event_id = f"{originator}:{event.seq_no}:{event.event_type.value}:{event.user_id}"
        if self._gossip_dispatcher._mark_seen(event_id):
            logger.debug("discovery_gossip_duplicate sender=%s event_id=%s", sender_id, event_id)
            return {"handled": True}

        logger.info(
            "discovery_gossip_new sender=%s event_type=%s user_id=%s seq_no=%d",
            sender_id, event.event_type.value, event.user_id, event.seq_no,
        )
        self.service.apply_remote_event(event)
        return {"handled": True}

    def _handle_heartbeat(self, sender_id: str) -> dict:
        snap = self.service.get_membership_snapshot()
        if sender_id not in snap.members:
            logger.debug("discovery_heartbeat_unknown_peer sender=%s", sender_id)
            return {"handled": True}
        logger.debug("discovery_heartbeat_recv from=%s", sender_id)
        self.service.heartbeat_member(sender_id)
        return {"handled": True}

    def _send_join_response(
        self, target_sender_id: str, *, accepted: bool, reason: Optional[str], events: list,
    ) -> None:
        """Build and send a JOIN_RESPONSE via Distribution's send_to_peer.
        Used by _handle_join_request on the seed side.
        """
        if self.broadcast_node is None:
            logger.error(
                "join_response_unsent target=%s — broadcast_node not set; "
                "DiscoveryNode was constructed without it",
                target_sender_id,
            )
            return
        try:
            host, port_str = target_sender_id.rsplit(":", 1)
            port = int(port_str)
        except ValueError:
            logger.warning(
                "join_response_bad_target target=%s — expected host:port",
                target_sender_id,
            )
            return

        from distribution.message import Message
        from peer_discovery.network.protocol import encode_discovery_envelope

        payload = {"accepted": accepted, "events": events}
        if reason is not None:
            payload["reason"] = reason

        content = encode_discovery_envelope(
            subtype=SUBTYPE_JOIN_RESPONSE,
            sender_pub_pem=self.public_key_pem,
            payload=payload,
        )
        response = Message(content=content, sender=self.advertise_address)
        self.broadcast_node.send_to_peer(host, port, response)
        logger.info(
            "discovery_join_response_sent to=%s accepted=%s events=%d",
            target_sender_id, accepted, len(events),
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
        self._heartbeat_manager.stop()
        logger.info("stop_complete room=%s", self.room_id)

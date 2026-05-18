"""Gossip protocol for event dissemination.

Outbound only after the consolidation. Inbound gossip arrives via
Distribution's on_message → chat_service.message_received →
DiscoveryNode.handle_message → DiscoveryNode._handle_gossip, which uses
this dispatcher's ``_mark_seen`` for deduplication.
"""
import logging
from collections import OrderedDict
from typing import TYPE_CHECKING

from peer_discovery.membership.models import MembershipDelta, MembershipEvent

if TYPE_CHECKING:
    from peer_discovery.network.discovery_node import DiscoveryNode

logger = logging.getLogger(__name__)

MAX_SEEN_EVENTS = 10_000


class GossipDispatcher:
    """Drives outbound gossip of membership events through Distribution's
    BroadcastNode. Maintains an LRU of seen event IDs so we don't echo events
    we've already broadcast or received.
    """

    def __init__(self, node: 'DiscoveryNode'):
        self.node = node
        self.seen_event_ids: OrderedDict[str, bool] = OrderedDict()

    def _mark_seen(self, event_id: str) -> bool:
        """Mark event as seen. Returns True if it was already seen."""
        if event_id in self.seen_event_ids:
            # Refresh LRU position
            self.seen_event_ids.move_to_end(event_id)
            return True

        self.seen_event_ids[event_id] = True
        if len(self.seen_event_ids) > MAX_SEEN_EVENTS:
            self.seen_event_ids.popitem(last=False)
        return False

    def dispatch(self, event: MembershipEvent, delta: MembershipDelta | None) -> None:
        """Callback from MembershipService for every event the local
        coordinator produces. Remote-sourced events (those received via
        apply_remote_event) are not re-gossiped — Distribution's broadcast
        already handles fan-out and a single hop is enough.
        """
        if event.source == "remote":
            logger.debug(
                "gossip_skip_remote event_type=%s user_id=%s seq_no=%d",
                event.event_type.value, event.user_id, event.seq_no,
            )
            return

        logger.info(
            "gossip_dispatch_local event_type=%s user_id=%s seq_no=%d source=%s",
            event.event_type.value, event.user_id, event.seq_no, event.source,
        )
        self._gossip(event)

    def _gossip(self, event: MembershipEvent) -> None:
        """Broadcast a membership event to all known peers through
        Distribution's BroadcastNode. Distribution signs, fans out, ACKs
        and retries.
        """
        if self.node.broadcast_node is None:
            logger.warning(
                "gossip_skipped — no broadcast_node configured. "
                "Event %s:%s:%d not sent.",
                event.user_id, event.event_type.value, event.seq_no,
            )
            return

        originator = event.originator or event.user_id
        event_id = f"{originator}:{event.seq_no}:{event.event_type.value}:{event.user_id}"
        self._mark_seen(event_id)

        from distribution.message import Message
        from peer_discovery.network.protocol import (
            SUBTYPE_GOSSIP, encode_discovery_envelope,
        )

        content = encode_discovery_envelope(
            subtype=SUBTYPE_GOSSIP,
            sender_pub_pem=self.node.public_key_pem,
            payload={"event": event.to_dict()},
        )
        gossip_msg = Message(content=content, sender=self.node.advertise_address)

        try:
            self.node.broadcast_node.broadcast(gossip_msg)
            snap = self.node.service.get_membership_snapshot()
            logger.info(
                "gossip_fanout event_type=%s user_id=%s seq_no=%d "
                "via=BroadcastNode.broadcast active_members=%d",
                event.event_type.value, event.user_id, event.seq_no,
                snap.active_count,
            )
        except Exception as exc:
            logger.warning(
                "gossip_broadcast_failed event_type=%s user_id=%s err=%s",
                event.event_type.value, event.user_id, exc,
            )

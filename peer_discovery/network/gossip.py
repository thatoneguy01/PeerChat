"""Gossip protocol for membership-event dissemination.

The dispatcher is **outbound only** after the consolidation. When the
local coordinator fires a membership delta (JOIN_ACCEPTED, LEAVE_CONFIRMED,
HISTORY_BACKFILL_COMPLETE, DISCONNECT_SUSPECTED, ...), this class wraps it
in a ``SUBTYPE_GOSSIP`` envelope and broadcasts it via Distribution's
``BroadcastNode.broadcast`` so every reachable peer applies the same delta.

Inbound gossip arrives the other way: Distribution's WS handler →
``on_message`` → ``chat_service.message_received`` →
``DiscoveryNode.handle_message`` → ``DiscoveryNode._handle_gossip``, which
calls this dispatcher's :py:meth:`_mark_seen` to dedup before applying.

**Dedup key:** ``f"{originator}:{seq_no}:{event_type}:{user_id}"``, kept in
an LRU of 10,000 entries. Events with ``source == "remote"`` are not
re-gossiped — that's the cycle-breaker. The same dedup key shape is used
by the duplicate guard in :py:mod:`peer_discovery.membership.duplicate_guard`
so a remote event can never be applied twice even across restarts.
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

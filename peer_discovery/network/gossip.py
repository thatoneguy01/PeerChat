"""Gossip protocol for event dissemination."""
import json
import logging
from collections import OrderedDict
from typing import TYPE_CHECKING

from peer_discovery.membership.models import MembershipDelta, MembershipEvent
from peer_discovery.network.protocol import MessageType, NetworkMessage

if TYPE_CHECKING:
    from peer_discovery.network.discovery_node import DiscoveryNode

logger = logging.getLogger(__name__)

MAX_SEEN_EVENTS = 10_000


class GossipDispatcher:
    def __init__(self, node: 'DiscoveryNode'):
        self.node = node
        self.seen_event_ids: OrderedDict[str, bool] = OrderedDict()

    def _mark_seen(self, event_id: str) -> bool:
        """Mark event as seen. Returns True if it was already seen."""
        if event_id in self.seen_event_ids:
            # Move to end to refresh LRU
            self.seen_event_ids.move_to_end(event_id)
            return True
            
        self.seen_event_ids[event_id] = True
        if len(self.seen_event_ids) > MAX_SEEN_EVENTS:
            self.seen_event_ids.popitem(last=False)
        return False

    def dispatch(self, event: MembershipEvent, delta: MembershipDelta | None) -> None:
        """Callback from MembershipService for outgoing events."""
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
        self._gossip(event, skip_peer=None)

    def handle_incoming_gossip(self, source_ip: str, msg: NetworkMessage) -> None:
        """Handle incoming EVENT_BROADCAST message."""
        payload = msg.payload.get("event")
        if not payload:
            logger.warning(
                "gossip_recv_no_payload from=%s sender=%s", source_ip, msg.sender_id,
            )
            return

        try:
            event = MembershipEvent.from_dict(payload)
        except Exception as e:
            logger.warning(
                "gossip_parse_failed from=%s sender=%s err=%s",
                source_ip, msg.sender_id, e,
            )
            return

        originator = event.originator or event.user_id
        event_id = f"{originator}:{event.seq_no}:{event.event_type.value}:{event.user_id}"

        if self._mark_seen(event_id):
            logger.debug(
                "gossip_recv_duplicate from=%s event_id=%s — already seen, dropped",
                msg.sender_id, event_id,
            )
            return

        logger.info(
            "gossip_recv_new from=%s event_type=%s user_id=%s seq_no=%d",
            msg.sender_id, event.event_type.value, event.user_id, event.seq_no,
        )
        self.node.service.apply_remote_event(event)

        # Forward gossip to other peers
        self._gossip(event, skip_peer=msg.sender_id)

    def _gossip(self, event: MembershipEvent, skip_peer: str | None = None) -> None:
        """Broadcast an event to all known active/suspected peers."""
        originator = event.originator or event.user_id
        event_id = f"{originator}:{event.seq_no}:{event.event_type.value}:{event.user_id}"
        self._mark_seen(event_id)

        msg = NetworkMessage(
            message_type=MessageType.EVENT_BROADCAST,
            sender_id=self.node.advertise_address,
            payload={"event": event.to_dict()}
        )

        snap = self.node.service.get_membership_snapshot()
        targets = []

        for member_id, info in snap.members.items():
            if member_id == self.node.advertise_address:
                continue
            if skip_peer and member_id == skip_peer:
                continue

            try:
                host, port_str = member_id.split(":")
                port = int(port_str)
            except ValueError:
                logger.warning("gossip_bad_member_id member_id=%s — skipped", member_id)
                continue

            targets.append((host, port, member_id))
            self.node.listener._executor.submit(
                self._send_fire_and_forget, host, port, msg, member_id,
            )

        if targets:
            logger.info(
                "gossip_fanout event_type=%s user_id=%s seq_no=%d targets=%d",
                event.event_type.value, event.user_id, event.seq_no, len(targets),
            )
        else:
            logger.debug(
                "gossip_no_targets event_type=%s user_id=%s seq_no=%d "
                "(no other members yet)",
                event.event_type.value, event.user_id, event.seq_no,
            )

    def _send_fire_and_forget(
        self, host: str, port: int, msg: NetworkMessage, target_id: str | None = None,
    ) -> None:
        """Send a message without waiting for a response."""
        from websockets.sync.client import connect as ws_connect
        from peer_discovery.network.protocol import encode_message

        target = target_id or f"{host}:{port}"
        uri = f"ws://{host}:{port}/"
        try:
            with ws_connect(uri, open_timeout=5.0, close_timeout=1.0) as ws:
                ws.send(encode_message(msg))
                logger.debug("gossip_sent_ok to=%s type=%s", target, msg.message_type.value)
        except Exception as e:
            logger.warning(
                "gossip_send_failed to=%s type=%s err=%s",
                target, msg.message_type.value, e,
            )

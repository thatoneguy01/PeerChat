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
            # We already gossiped this when it was received if it was new.
            # But wait, the plan says:
            # "Outgoing Gossip: For every MembershipEvent, if event.source != 'remote', broadcast"
            return
            
        self._gossip(event, skip_peer=None)

    def handle_incoming_gossip(self, source_ip: str, msg: NetworkMessage) -> None:
        """Handle incoming EVENT_BROADCAST message."""
        payload = msg.payload.get("event")
        if not payload:
            return
            
        try:
            event = MembershipEvent.from_dict(payload)
        except Exception as e:
            logger.warning("Failed to parse incoming gossip from %s: %s", msg.sender_id, e)
            return
            
        # event_id = f"{originator}:{seq_no}:{event_type}:{user_id}"
        originator = event.originator or event.user_id
        event_id = f"{originator}:{event.seq_no}:{event.event_type.value}:{event.user_id}"
        
        if self._mark_seen(event_id):
            return  # Already seen, drop
            
        # Apply remote event (will be deduped by internal state machine if already applied)
        self.node.service.apply_remote_event(event)
        
        # Forward gossip to other peers
        self._gossip(event, skip_peer=msg.sender_id)

    def _gossip(self, event: MembershipEvent, skip_peer: str | None = None) -> None:
        """Broadcast an event to all known active/suspected peers."""
        # Mark local events as seen so we don't bounce them back if received later
        originator = event.originator or event.user_id
        event_id = f"{originator}:{event.seq_no}:{event.event_type.value}:{event.user_id}"
        self._mark_seen(event_id)
        
        # Prepare message
        msg = NetworkMessage(
            message_type=MessageType.EVENT_BROADCAST,
            sender_id=self.node.advertise_address,
            payload={"event": event.to_dict()}
        )
        
        # Get peers
        snap = self.node.service.get_membership_snapshot()
        
        for member_id, info in snap.members.items():
            if member_id == self.node.advertise_address:
                continue
            if skip_peer and member_id == skip_peer:
                continue
                
            try:
                host, port_str = member_id.split(":")
                port = int(port_str)
            except ValueError:
                continue
                
            # Fire and forget
            # Submit to ThreadPoolExecutor to avoid blocking the caller
            self.node.listener._executor.submit(
                self._send_fire_and_forget, host, port, msg
            )

    def _send_fire_and_forget(self, host: str, port: int, msg: NetworkMessage) -> None:
        """Send a message without waiting for a response."""
        import socket
        from peer_discovery.network.framing import send_framed
        from peer_discovery.network.protocol import encode_message
        
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(5.0)  # Short timeout for fire and forget
                sock.connect((host, port))
                send_framed(sock, encode_message(msg))
        except Exception as e:
            logger.debug("Failed to gossip to %s:%d - %s", host, port, e)

"""Bootstrap logic for joining the network."""
import base64
import json
import logging
from typing import TYPE_CHECKING

from peer_discovery.network.protocol import MessageType, NetworkMessage

if TYPE_CHECKING:
    from peer_discovery.network.discovery_node import DiscoveryNode

logger = logging.getLogger(__name__)


def attempt_bootstrap(node: 'DiscoveryNode', display_name: str) -> bool:
    """Attempt to join the network by contacting bootstrap peers.
    
    Returns True if successfully bootstrapped or if this is the first node.
    Returns False if all bootstrap peers failed.
    """
    if not node.config.bootstrap_peers:
        logger.info("No bootstrap peers configured. Starting as seed node.")
        # Join ourselves locally
        res = node.service.join_member(
            user_id=node.advertise_address,
            display_name=display_name,
            public_key=node.crypto.get_public_key_bytes()
        )
        return res.accepted

    for peer in node.config.bootstrap_peers:
        try:
            host, port_str = peer.split(":")
            port = int(port_str)
        except ValueError:
            logger.error("Invalid bootstrap peer format: %s. Expected host:port.", peer)
            continue

        req = NetworkMessage(
            message_type=MessageType.JOIN_REQUEST,
            sender_id=node.advertise_address,
            payload={
                "display_name": display_name,
                "public_key_b64": base64.b64encode(
                    node.crypto.get_public_key_bytes()
                ).decode(),
            },
        )

        logger.info("Attempting bootstrap via %s", peer)
        resp = node.client.send_and_receive(host, port, req)
        
        if not resp:
            logger.warning("No response from bootstrap peer %s", peer)
            continue
            
        if resp.message_type != MessageType.JOIN_RESPONSE:
            logger.warning("Unexpected response type %s from %s", resp.message_type, peer)
            continue
            
        status = resp.payload.get("status")
        if status != "accepted":
            reason = resp.payload.get("reason", "unknown")
            logger.error("Bootstrap rejected by %s: %s", peer, reason)
            return False
            
        # Decrypt snapshot
        encrypted_snapshot = resp.payload.get("encrypted_snapshot")
        if not encrypted_snapshot:
            logger.error("No encrypted_snapshot in accepted response from %s", peer)
            continue
            
        try:
            from peer_discovery.membership.models import MembershipEvent

            ciphertext = base64.b64decode(encrypted_snapshot)
            plaintext = node.crypto.decrypt(ciphertext)
            events_data = json.loads(plaintext.decode("utf-8"))
            
            events = [MembershipEvent.from_dict(d) for d in events_data]
            node.service.apply_remote_snapshot(events)
            
            logger.info("Successfully bootstrapped via %s. Applied %d events.", peer, len(events))
            return True
            
        except Exception as e:
            logger.error("Failed to process bootstrap response from %s: %s", peer, e)
            continue

    logger.error("Failed to connect to any bootstrap peers.")
    return False


def handle_join_request(node: 'DiscoveryNode', source_ip: str, msg: NetworkMessage) -> NetworkMessage:
    """Handle an incoming JOIN_REQUEST."""
    display_name = msg.payload.get("display_name", "Unknown")
    pk_b64 = msg.payload.get("public_key_b64")
    
    if not pk_b64:
        return NetworkMessage(
            message_type=MessageType.JOIN_RESPONSE,
            sender_id=node.advertise_address,
            payload={"status": "rejected", "reason": "Missing public_key_b64"}
        )
        
    try:
        pub_key = base64.b64decode(pk_b64)
    except Exception:
        return NetworkMessage(
            message_type=MessageType.JOIN_RESPONSE,
            sender_id=node.advertise_address,
            payload={"status": "rejected", "reason": "Invalid public_key encoding"}
        )
        
    # Attempt to join locally
    context = {
        "source_address": source_ip,
        "public_key": pub_key,
    }
    
    res = node.service.join_member(
        user_id=msg.sender_id,
        display_name=display_name,
        public_key=pub_key,
        context=context
    )
    
    if not res.accepted:
        return NetworkMessage(
            message_type=MessageType.JOIN_RESPONSE,
            sender_id=node.advertise_address,
            payload={"status": "rejected", "reason": res.reason}
        )
        
    # Accepted! Serialize snapshot
    snap = node.service.get_membership_snapshot()
    # We need to send the event log, not just the snapshot state, so the remote node can replay.
    # The plan says "snapshot: [event1, event2, ...]"
    events = node.service._coordinator._log.get_events_since(0)
    events_data = [e.to_dict() for e in events]
    
    plaintext = json.dumps(events_data).encode("utf-8")
    
    # Encrypt with requester's public key
    try:
        ciphertext = node.crypto.encrypt_for(plaintext, pub_key)
        encrypted_b64 = base64.b64encode(ciphertext).decode()
    except Exception as e:
        logger.error("Failed to encrypt snapshot for %s: %s", msg.sender_id, e)
        return NetworkMessage(
            message_type=MessageType.JOIN_RESPONSE,
            sender_id=node.advertise_address,
            payload={"status": "error", "reason": "Crypto failure on server"}
        )
        
    return NetworkMessage(
        message_type=MessageType.JOIN_RESPONSE,
        sender_id=node.advertise_address,
        payload={
            "status": "accepted",
            "encrypted_snapshot": encrypted_b64
        }
    )

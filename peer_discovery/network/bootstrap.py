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
        logger.info(
            "bootstrap mode=SEED (no bootstrap_peers configured) — joining self as %s",
            node.advertise_address,
        )
        res = node.service.join_member(
            user_id=node.advertise_address,
            display_name=display_name,
            public_key=node.crypto.get_public_key_bytes(),
        )
        logger.info(
            "bootstrap_seed_join accepted=%s seq_no=%d version=%d reason=%s",
            res.accepted, res.seq_no, res.membership_version, res.reason,
        )
        return res.accepted

    logger.info(
        "bootstrap mode=JOINER peers=%s timeout=%.1fs",
        node.config.bootstrap_peers, node.config.bootstrap_timeout,
    )

    for peer in node.config.bootstrap_peers:
        try:
            host, port_str = peer.split(":")
            port = int(port_str)
        except ValueError:
            logger.error("bootstrap_invalid_peer peer=%s expected_format=host:port", peer)
            continue

        pubkey_b64 = base64.b64encode(node.crypto.get_public_key_bytes()).decode()
        req = NetworkMessage(
            message_type=MessageType.JOIN_REQUEST,
            sender_id=node.advertise_address,
            payload={"display_name": display_name, "public_key_b64": pubkey_b64},
        )

        logger.info(
            "bootstrap_attempt peer=%s sender_id=%s display_name=%s pubkey_bytes=%d",
            peer, node.advertise_address, display_name,
            len(node.crypto.get_public_key_bytes()),
        )

        try:
            resp = node.client.send_and_receive(host, port, req)
        except Exception as e:
            logger.warning(
                "bootstrap_send_failed peer=%s err=%s — likely firewall, AP isolation, "
                "or seed not running yet",
                peer, e,
            )
            continue

        if not resp:
            logger.warning("bootstrap_no_response peer=%s — TCP closed before reply", peer)
            continue

        if resp.message_type != MessageType.JOIN_RESPONSE:
            logger.warning(
                "bootstrap_wrong_reply_type peer=%s got=%s expected=JOIN_RESPONSE",
                peer, resp.message_type,
            )
            continue

        status = resp.payload.get("status")
        if status != "accepted":
            reason = resp.payload.get("reason", "unknown")
            logger.error("bootstrap_rejected peer=%s reason=%s", peer, reason)
            return False

        encrypted_snapshot = resp.payload.get("encrypted_snapshot")
        if not encrypted_snapshot:
            logger.error("bootstrap_response_missing_snapshot peer=%s", peer)
            continue

        try:
            from peer_discovery.membership.models import MembershipEvent

            ciphertext = base64.b64decode(encrypted_snapshot)
            logger.info(
                "bootstrap_decrypting peer=%s ciphertext_bytes=%d", peer, len(ciphertext),
            )
            plaintext = node.crypto.decrypt(ciphertext)
            events_data = json.loads(plaintext.decode("utf-8"))
            events = [MembershipEvent.from_dict(d) for d in events_data]

            logger.info(
                "bootstrap_applying_snapshot peer=%s events=%d types=%s",
                peer, len(events),
                [e.event_type.value for e in events],
            )
            node.service.apply_remote_snapshot(events)

            snap = node.service.get_membership_snapshot()
            logger.info(
                "bootstrap_success peer=%s applied=%d members_now=%d active_now=%d",
                peer, len(events), len(snap.members), snap.active_count,
            )
            return True

        except Exception as e:
            logger.error(
                "bootstrap_decrypt_or_apply_failed peer=%s err=%s — likely "
                "wrong seed pubkey or snapshot corruption",
                peer, e,
            )
            continue

    logger.error(
        "bootstrap_all_peers_failed peers=%s — node will run isolated",
        node.config.bootstrap_peers,
    )
    return False


def handle_join_request(node: 'DiscoveryNode', source_ip: str, msg: NetworkMessage) -> NetworkMessage:
    """Handle an incoming JOIN_REQUEST."""
    display_name = msg.payload.get("display_name", "Unknown")
    pk_b64 = msg.payload.get("public_key_b64")

    logger.info(
        "join_request_received source_ip=%s sender_id=%s display_name=%s pubkey_present=%s",
        source_ip, msg.sender_id, display_name, bool(pk_b64),
    )

    if not pk_b64:
        logger.warning(
            "join_request_rejected source_ip=%s sender_id=%s reason=missing_public_key_b64",
            source_ip, msg.sender_id,
        )
        return NetworkMessage(
            message_type=MessageType.JOIN_RESPONSE,
            sender_id=node.advertise_address,
            payload={"status": "rejected", "reason": "Missing public_key_b64"}
        )

    try:
        pub_key = base64.b64decode(pk_b64)
    except Exception as e:
        logger.warning(
            "join_request_rejected source_ip=%s sender_id=%s reason=bad_pubkey_b64 err=%s",
            source_ip, msg.sender_id, e,
        )
        return NetworkMessage(
            message_type=MessageType.JOIN_RESPONSE,
            sender_id=node.advertise_address,
            payload={"status": "rejected", "reason": "Invalid public_key encoding"}
        )

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
        logger.warning(
            "join_request_rejected_by_validator sender_id=%s reason=%s",
            msg.sender_id, res.reason,
        )
        return NetworkMessage(
            message_type=MessageType.JOIN_RESPONSE,
            sender_id=node.advertise_address,
            payload={"status": "rejected", "reason": res.reason}
        )

    snap = node.service.get_membership_snapshot()
    events = node.service._coordinator._log.get_events_since(0)
    events_data = [e.to_dict() for e in events]
    plaintext = json.dumps(events_data).encode("utf-8")

    logger.info(
        "join_request_accepted sender_id=%s seq_no=%d members_now=%d events_to_send=%d "
        "plaintext_bytes=%d",
        msg.sender_id, res.seq_no, len(snap.members), len(events), len(plaintext),
    )

    try:
        ciphertext = node.crypto.encrypt_for(plaintext, pub_key)
        encrypted_b64 = base64.b64encode(ciphertext).decode()
    except Exception as e:
        logger.error(
            "join_request_encrypt_failed sender_id=%s err=%s — joiner's pubkey "
            "may be malformed or incompatible",
            msg.sender_id, e,
        )
        return NetworkMessage(
            message_type=MessageType.JOIN_RESPONSE,
            sender_id=node.advertise_address,
            payload={"status": "error", "reason": "Crypto failure on server"}
        )

    logger.info(
        "join_response_sending to=%s ciphertext_bytes=%d events=%d",
        msg.sender_id, len(ciphertext), len(events),
    )

    return NetworkMessage(
        message_type=MessageType.JOIN_RESPONSE,
        sender_id=node.advertise_address,
        payload={
            "status": "accepted",
            "encrypted_snapshot": encrypted_b64
        }
    )

"""Bootstrap logic for joining the network.

The handshake is plaintext over WebSocket. Nothing on the wire is secret:

  - JOIN_REQUEST carries the joiner's display name and public key. The pubkey
    is public by definition; that is the whole point of distributing it.
  - JOIN_RESPONSE carries the current membership event log (which, when
    applied, hydrates the joiner's snapshot — including every member's
    public key, address, and state).

Chat-message confidentiality and signing live at the Distribution+Security
layer, not here. Discovery's job is purely to publish "who is in the room
and what is their public key."
"""
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
            public_key=node.public_key_pem,
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

        pubkey_b64 = base64.b64encode(node.public_key_pem).decode()
        req = NetworkMessage(
            message_type=MessageType.JOIN_REQUEST,
            sender_id=node.advertise_address,
            payload={"display_name": display_name, "public_key_b64": pubkey_b64},
        )

        logger.info(
            "bootstrap_attempt peer=%s sender_id=%s display_name=%s pubkey_bytes=%d",
            peer, node.advertise_address, display_name, len(node.public_key_pem),
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
            logger.warning("bootstrap_no_response peer=%s — connection closed before reply", peer)
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

        events_data = resp.payload.get("events")
        if events_data is None:
            logger.error("bootstrap_response_missing_events peer=%s", peer)
            continue

        try:
            from peer_discovery.membership.models import MembershipEvent

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
                "bootstrap_apply_failed peer=%s err=%s — likely a "
                "malformed snapshot payload",
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

    # The pubkey is informational — discovery's handshake is plaintext and
    # doesn't need it for crypto. We still record it on MemberInfo so other
    # peers (and Distribution's verify()) can look it up. An empty pubkey
    # means chat verify() will fail for this peer, but discovery itself is
    # fine; we accept the join with an empty key and log a warning.
    pub_key = b""
    if pk_b64:
        try:
            pub_key = base64.b64decode(pk_b64)
        except Exception as e:
            logger.warning(
                "join_request_bad_pubkey_b64 source_ip=%s sender_id=%s err=%s — "
                "accepting join with empty pubkey",
                source_ip, msg.sender_id, e,
            )
            pub_key = b""
    else:
        logger.warning(
            "join_request_no_pubkey source_ip=%s sender_id=%s — accepting join "
            "but chat verify() will fail for this peer",
            source_ip, msg.sender_id,
        )

    context = {
        "source_address": source_ip,
        "public_key": pub_key,
    }

    res = node.service.join_member(
        user_id=msg.sender_id,
        display_name=display_name,
        public_key=pub_key,
        context=context,
    )

    if not res.accepted:
        logger.warning(
            "join_request_rejected_by_validator sender_id=%s reason=%s",
            msg.sender_id, res.reason,
        )
        return NetworkMessage(
            message_type=MessageType.JOIN_RESPONSE,
            sender_id=node.advertise_address,
            payload={"status": "rejected", "reason": res.reason},
        )

    snap = node.service.get_membership_snapshot()
    events = node.service._coordinator._log.get_events_since(0)
    events_data = [e.to_dict() for e in events]

    logger.info(
        "join_request_accepted sender_id=%s seq_no=%d members_now=%d events_to_send=%d",
        msg.sender_id, res.seq_no, len(snap.members), len(events),
    )

    logger.info(
        "join_response_sending to=%s events=%d",
        msg.sender_id, len(events),
    )

    return NetworkMessage(
        message_type=MessageType.JOIN_RESPONSE,
        sender_id=node.advertise_address,
        payload={
            "status": "accepted",
            "events": events_data,
        },
    )

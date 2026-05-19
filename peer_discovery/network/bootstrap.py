"""Bootstrap logic for joining the network.

The joiner sends its JOIN_REQUEST through Distribution's BroadcastNode (port
5678). The response arrives asynchronously via Distribution's on_message →
chat_service.message_received → DiscoveryNode.handle_message →
DiscoveryNode._handle_join_response — which sets a threading.Event keyed by
the seed's bootstrap_peers entry. attempt_bootstrap waits on that Event
with bootstrap_timeout.
"""
import logging
import threading
from typing import TYPE_CHECKING

from peer_discovery.network.protocol import (
    SUBTYPE_JOIN_REQUEST,
    encode_discovery_envelope,
)

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

    if node.broadcast_node is None:
        logger.error(
            "bootstrap_no_broadcast_node — DiscoveryNode was constructed "
            "without a BroadcastNode reference. Cannot join the network."
        )
        return False

    return _attempt_bootstrap_via_broadcast_node(node, display_name)


def _attempt_bootstrap_via_broadcast_node(node: 'DiscoveryNode', display_name: str) -> bool:
    """Send JOIN_REQUEST through Distribution's BroadcastNode.send_to_peer.

    The response (JOIN_RESPONSE) arrives asynchronously via on_message →
    DiscoveryNode.handle_message → _handle_join_response, which sets a
    threading.Event keyed by the seed's advertise_address. We wait on it
    with bootstrap_timeout. Each peer is tried in order until one succeeds.
    """
    from distribution.message import Message

    timeout = node.config.bootstrap_timeout or 5.0

    for peer in node.config.bootstrap_peers:
        try:
            host, port_str = peer.split(":")
            port = int(port_str)
        except ValueError:
            logger.error(
                "bootstrap_invalid_peer peer=%s expected_format=host:port", peer,
            )
            continue

        # Register a pending-response Event keyed by the seed's address.
        # _handle_join_response will set this when JOIN_RESPONSE arrives.
        event = threading.Event()
        with node._pending_joins_lock:
            node._pending_joins[peer] = event

        try:
            req_content = encode_discovery_envelope(
                subtype=SUBTYPE_JOIN_REQUEST,
                sender_pub_pem=node.public_key_pem,
                payload={"display_name": display_name},
            )
            req = Message(content=req_content, sender=node.advertise_address)

            logger.info(
                "bootstrap_attempt peer=%s sender_id=%s display_name=%s pubkey_bytes=%d "
                "via=BroadcastNode.send_to_peer",
                peer, node.advertise_address, display_name, len(node.public_key_pem),
            )

            try:
                node.broadcast_node.send_to_peer(host, port, req)
            except Exception as e:
                logger.warning(
                    "bootstrap_send_failed peer=%s err=%s — likely firewall, "
                    "AP isolation, or seed not running yet",
                    peer, e,
                )
                continue

            # Wait for the seed's JOIN_RESPONSE to arrive (sets the Event).
            if event.wait(timeout=timeout):
                snap = node.service.get_membership_snapshot()
                if snap.active_count > 0:
                    logger.info(
                        "bootstrap_success peer=%s members_now=%d active_now=%d",
                        peer, len(snap.members), snap.active_count,
                    )
                    return True
                # Event fired but no active members — the response was a
                # rejection (see earlier discovery_join_response_rejected log).
                logger.error("bootstrap_response_was_rejection peer=%s", peer)
                continue
            else:
                logger.warning(
                    "bootstrap_no_response peer=%s — no JOIN_RESPONSE within %.1fs",
                    peer, timeout,
                )
                continue
        finally:
            with node._pending_joins_lock:
                node._pending_joins.pop(peer, None)

    logger.error(
        "bootstrap_all_peers_failed peers=%s — node will run isolated",
        node.config.bootstrap_peers,
    )
    return False


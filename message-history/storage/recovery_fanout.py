import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple


def request_missing_history_from_all_peers(
    streamer,
    requester_host: str,
    requester_port: int,
    peer_addresses: Optional[Iterable[Tuple[str, int]]] = None,
    transfer_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Ask every active peer to send history missing from this node.

    This module owns the "all active peers" recovery decision. The existing
    HistoryChunkStreamer still owns the transport payload, chunking, direct
    send_to_peer(), and target-side chunk ingestion.
    """
    transfer_id = transfer_id or str(uuid.uuid4())
    have_vector_clock = streamer.store.get_latest_vector_clock()
    targets = _resolve_recovery_targets(
        streamer=streamer,
        peer_addresses=peer_addresses,
        requester_host=requester_host,
        requester_port=requester_port,
    )

    for provider_host, provider_port in targets:
        streamer.send_recover_request(
            provider_host=provider_host,
            provider_port=provider_port,
            requester_host=requester_host,
            requester_port=requester_port,
            transfer_id=transfer_id,
        )

    return {
        "transfer_id": transfer_id,
        "requester_host": requester_host,
        "requester_port": requester_port,
        "have_vector_clock": have_vector_clock,
        "peers_requested": len(targets),
        "targets": [
            {"host": host, "port": port}
            for host, port in targets
        ],
    }


def _resolve_recovery_targets(
    streamer,
    peer_addresses: Optional[Iterable[Tuple[str, int]]],
    requester_host: str,
    requester_port: int,
) -> List[Tuple[str, int]]:
    if peer_addresses is None:
        peer_addresses = _registry_peers(streamer)

    requester_addr = f"{requester_host}:{requester_port}"
    excluded = {requester_addr, streamer.self_user_id}
    targets: List[Tuple[str, int]] = []
    seen: set[str] = set()

    for peer in peer_addresses:
        try:
            host = str(peer[0])
            port = int(peer[1])
        except (IndexError, TypeError, ValueError):
            continue

        addr = f"{host}:{port}"
        if addr in excluded or addr in seen:
            continue

        targets.append((host, port))
        seen.add(addr)

    return targets


def _registry_peers(streamer) -> List[Tuple[str, int]]:
    registry = getattr(streamer.broadcaster, "peer_registry", None)
    if registry is None:
        return []

    try:
        return list(registry.get_peers())
    except Exception:
        return []

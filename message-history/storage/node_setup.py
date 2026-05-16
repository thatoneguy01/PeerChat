import logging
from dataclasses import dataclass
from typing import Any

from .local_message_store import LocalMessageStore
from .listeners import Listeners
from .models import Message
from .recovery_fanout import request_missing_history_from_all_peers
from .recovery_stream import HistoryChunkStreamer

logger = logging.getLogger(__name__)


@dataclass
class NodeWiring:
    store: LocalMessageStore
    streamer: HistoryChunkStreamer
    listeners: Listeners


def wire_node(
    node,
    host: str,
    port: int,
    *,
    pull_recovery_on_start: bool = True,
) -> NodeWiring:
    """Build storage + recovery wiring for a BroadcastNode."""
    self_user_id = f"{host}:{port}"

    store = LocalMessageStore()
    _sync_node_vector_clock(node, store)
    streamer = HistoryChunkStreamer(
        store=store,
        broadcaster=node,
        self_user_id=self_user_id,
    )
    listeners = Listeners()

    storage_listener = _make_storage_listener(streamer, store, node)
    listeners.register(storage_listener)
    node.on_message = listeners.dispatch

    node.start()

    if pull_recovery_on_start:
        try:
            request_missing_history_from_all_peers(
                streamer=streamer,
                requester_host=host,
                requester_port=port,
            )
        except Exception as exc:
            logger.warning("self-recovery on start failed: %s", exc)

    return NodeWiring(
        store=store,
        streamer=streamer,
        listeners=listeners,
    )


def _sync_node_vector_clock(node, store: LocalMessageStore) -> None:
    latest_vc = store.get_latest_vector_clock()
    node_vc = getattr(node, "_vc", None)
    if latest_vc and node_vc is not None:
        node_vc.merge(latest_vc)


def _make_storage_listener(
    streamer: HistoryChunkStreamer,
    store: LocalMessageStore,
    node=None,
):
    def storage_listener(transport_msg: Any) -> None:
        result = streamer.handle_transport_message(transport_msg)
        if result.get("handled"):
            if (
                result.get("type") == "history_chunk"
                and result.get("is_last")
                and node is not None
                and hasattr(node, "sync_vector_clock")
            ):
                node.sync_vector_clock(store.get_latest_vector_clock())
            return

        try:
            store.save(_to_storage_message(transport_msg))
        except Exception as exc:
            logger.warning("store.save raised: %s", exc)

    return storage_listener


def _to_storage_message(transport_msg: Any) -> Message:
    return Message(
        id=transport_msg.id,
        content=transport_msg.content,
        sender=transport_msg.sender,
        timestamp=transport_msg.timestamp,
        signature=getattr(transport_msg, "signature", ""),
        ttl=getattr(transport_msg, "ttl", 0),
        vector_clock=dict(getattr(transport_msg, "vector_clock", {})),
    )

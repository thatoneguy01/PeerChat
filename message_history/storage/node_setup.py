import logging
from dataclasses import dataclass
from typing import Any

from .local_message_store import LocalMessageStore
from .models import Message
from .recovery_stream import HistoryChunkStreamer

logger = logging.getLogger(__name__)


@dataclass
class NodeWiring:
    store: LocalMessageStore
    streamer: HistoryChunkStreamer


def wire_node(
    node,
    host: str,
    port: int,
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
    return NodeWiring(
        store=store,
        streamer=streamer,
    )


def _sync_node_vector_clock(node, store: LocalMessageStore) -> None:
    latest_vc = store.get_latest_vector_clock()
    node_vc = getattr(node, "_vc", None)
    if latest_vc and node_vc is not None:
        node_vc.merge(latest_vc)


def handle_storage_message(
    streamer: HistoryChunkStreamer,
    store: LocalMessageStore,
    transport_msg: Any,
    node=None,
) -> dict:
    result = streamer.handle_transport_message(transport_msg)
    if result.get("handled"):
        if (
            result.get("type") == "history_chunk"
            and result.get("is_last")
            and node is not None
            and hasattr(node, "sync_vector_clock")
        ):
            node.sync_vector_clock(store.get_latest_vector_clock())
        return result

    try:
        store.save(_to_storage_message(transport_msg))
        return {"handled": False, "type": "chat", "saved": True}
    except Exception as exc:
        logger.warning("store.save raised: %s", exc)
        return {"handled": False, "type": "chat", "saved": False, "error": str(exc)}


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

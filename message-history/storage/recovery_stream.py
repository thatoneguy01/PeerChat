import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .local_message_store import LocalMessageStore
from .models import Message


HISTORY_CHUNK = "history_chunk"


def _default_message_factory():
    try:
        from distribution import Message as DistributionMessage
    except ModuleNotFoundError:
        repo_root = Path(__file__).resolve().parents[2]
        sys.path.append(str(repo_root))
        from distribution import Message as DistributionMessage

    return DistributionMessage


class HistoryChunkStreamer:
    """
    Streams history chunks through the existing Message Distribution transport.

    BroadcastNode only supports broadcast fanout today, so every chunk carries a
    target_user_id. Non-target peers can receive the transport message but ignore
    the recovery payload.
    """

    def __init__(
        self,
        store: LocalMessageStore,
        broadcaster,
        self_user_id: str,
        message_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.store = store
        self.broadcaster = broadcaster
        self.self_user_id = self_user_id
        self.message_factory = message_factory or _default_message_factory()

    def stream_missing_history(
        self,
        target_user_id: str,
        have_vector_clock: Dict[str, int],
        transfer_id: Optional[str] = None,
        chunk_size: int = 100,
        delay_seconds: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Send missing messages to target_user_id one chunk at a time.

        Returns lightweight stats that callers can log or expose in tests.
        """
        transfer_id = transfer_id or str(uuid.uuid4())
        chunks = self.store.build_history_chunks(
            have_vector_clock=have_vector_clock,
            transfer_id=transfer_id,
            chunk_size=chunk_size,
        )

        for chunk in chunks:
            payload = dict(chunk)
            payload["target_user_id"] = target_user_id
            payload["source_user_id"] = self.self_user_id

            transport_message = self.message_factory(
                content=json.dumps(payload, separators=(",", ":")),
                sender=self.self_user_id,
            )
            self.broadcaster.broadcast(transport_message)

            if delay_seconds > 0:
                time.sleep(delay_seconds)

        return {
            "transfer_id": transfer_id,
            "target_user_id": target_user_id,
            "chunks_sent": len(chunks),
            "messages_sent": sum(len(chunk["messages"]) for chunk in chunks),
        }

    def handle_transport_message(self, transport_message) -> Dict[str, Any]:
        """
        Try to ingest a recovery chunk from a distribution Message.

        Non-recovery chat messages and chunks for other peers are ignored.
        """
        try:
            payload = json.loads(transport_message.content)
        except (TypeError, json.JSONDecodeError):
            return {"handled": False, "reason": "not_json"}

        if payload.get("type") != HISTORY_CHUNK:
            return {"handled": False, "reason": "not_history_chunk"}

        if payload.get("target_user_id") != self.self_user_id:
            return {"handled": False, "reason": "not_target"}

        messages = []
        for raw_message in payload.get("messages", []):
            try:
                messages.append(Message.from_dict(raw_message))
            except (KeyError, TypeError, ValueError):
                messages.append(raw_message)

        result = self.store.save_many(messages)
        return {
            "handled": True,
            "transfer_id": payload.get("transfer_id"),
            "chunk_id": payload.get("chunk_id"),
            "is_last": bool(payload.get("is_last", False)),
            **result,
        }

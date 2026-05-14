import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .local_message_store import LocalMessageStore
from .models import Message


HISTORY_CHUNK = "history_chunk"
RECOVER_REQUEST = "recover_request"


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

    Uses BroadcastNode.send_to_peer() so each recovery chunk goes only to the
    recovering peer and is not fanned out to the whole room.
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
        target_host: str,
        target_port: int,
        have_vector_clock: Dict[str, int],
        transfer_id: Optional[str] = None,
        chunk_size: int = 100,
        delay_seconds: float = 0.0,
    ) -> Dict[str, Any]:
        """
        Send missing messages to one peer one chunk at a time.

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
            payload["source_user_id"] = self.self_user_id

            transport_message = self.message_factory(
                content=json.dumps(payload, separators=(",", ":")),
                sender=self.self_user_id,
            )
            self.broadcaster.send_to_peer(target_host, target_port, transport_message)

            if delay_seconds > 0:
                time.sleep(delay_seconds)

        return {
            "transfer_id": transfer_id,
            "target_host": target_host,
            "target_port": target_port,
            "chunks_sent": len(chunks),
            "messages_sent": sum(len(chunk["messages"]) for chunk in chunks),
        }

    def send_recover_request(
        self,
        provider_host: str,
        provider_port: int,
        requester_host: str,
        requester_port: int,
        transfer_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Ask one provider peer to stream the history this node is missing.

        The request carries this node's latest vector clock so the provider can
        avoid sending messages already stored locally.
        """
        transfer_id = transfer_id or str(uuid.uuid4())
        payload = {
            "type": RECOVER_REQUEST,
            "transfer_id": transfer_id,
            "requester_id": self.self_user_id,
            "requester_host": requester_host,
            "requester_port": requester_port,
            "have_vector_clock": self.store.get_latest_vector_clock(),
        }

        transport_message = self.message_factory(
            content=json.dumps(payload, separators=(",", ":")),
            sender=self.self_user_id,
        )
        self.broadcaster.send_to_peer(provider_host, provider_port, transport_message)

        return {
            "transfer_id": transfer_id,
            "provider_host": provider_host,
            "provider_port": provider_port,
            "requester_host": requester_host,
            "requester_port": requester_port,
            "have_vector_clock": payload["have_vector_clock"],
        }

    def handle_transport_message(self, transport_message) -> Dict[str, Any]:
        """
        Handle a recovery transport message from Distribution.

        Supports both recover_request and history_chunk payloads. Non-recovery
        chat messages are ignored.
        """
        try:
            payload = json.loads(transport_message.content)
        except (TypeError, json.JSONDecodeError):
            return {"handled": False, "reason": "not_json"}

        payload_type = payload.get("type")
        if payload_type == RECOVER_REQUEST:
            return self._handle_recover_request(payload)
        if payload_type == HISTORY_CHUNK:
            return self._handle_history_chunk(payload)

        return {"handled": False, "reason": "not_recovery_message"}

    def _handle_recover_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            requester_host = str(payload["requester_host"])
            requester_port = int(payload["requester_port"])
        except (KeyError, TypeError, ValueError):
            return {"handled": False, "reason": "invalid_recover_request"}

        have_vector_clock = payload.get("have_vector_clock", {})
        if not isinstance(have_vector_clock, dict):
            have_vector_clock = {}

        stats = self.stream_missing_history(
            target_host=requester_host,
            target_port=requester_port,
            have_vector_clock=have_vector_clock,
            transfer_id=payload.get("transfer_id"),
        )
        return {
            "handled": True,
            "type": RECOVER_REQUEST,
            "requester_id": payload.get("requester_id"),
            **stats,
        }

    def _handle_history_chunk(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        messages = []
        for raw_message in payload.get("messages", []):
            try:
                messages.append(Message.from_dict(raw_message))
            except (KeyError, TypeError, ValueError):
                messages.append(raw_message)

        result = self.store.save_many(messages)
        return {
            "handled": True,
            "type": HISTORY_CHUNK,
            "transfer_id": payload.get("transfer_id"),
            "chunk_id": payload.get("chunk_id"),
            "is_last": bool(payload.get("is_last", False)),
            **result,
        }

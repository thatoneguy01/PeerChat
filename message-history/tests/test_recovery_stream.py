import json
import shutil
from pathlib import Path

import pytest

from storage import HistoryChunkStreamer, LocalMessageStore, Message


BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
INDEX_DIR = BASE_DIR / "index"


class FakeBroadcastMessage:
    def __init__(self, content: str, sender: str):
        self.content = content
        self.sender = sender


class FakeBroadcaster:
    def __init__(self):
        self.messages = []

    def broadcast(self, message):
        self.messages.append(message)


def make_message(
    msg_id: str,
    content: str,
    sender: str = "127.0.0.1:5001",
    seq: int = 1,
) -> Message:
    return Message(
        id=msg_id,
        content=content,
        sender=sender,
        timestamp=1747058342.71,
        signature="",
        ttl=10,
        vector_clock={sender: seq},
    )


@pytest.fixture(autouse=True)
def clean_storage():
    shutil.rmtree(LOG_DIR, ignore_errors=True)
    shutil.rmtree(INDEX_DIR, ignore_errors=True)
    yield
    shutil.rmtree(LOG_DIR, ignore_errors=True)
    shutil.rmtree(INDEX_DIR, ignore_errors=True)


def test_stream_missing_history_broadcasts_targeted_chunks():
    store = LocalMessageStore()
    for i in range(5):
        store.save(make_message(f"msg-{i + 1:03}", f"msg {i + 1}", seq=i + 1))

    broadcaster = FakeBroadcaster()
    streamer = HistoryChunkStreamer(
        store=store,
        broadcaster=broadcaster,
        self_user_id="127.0.0.1:5001",
        message_factory=FakeBroadcastMessage,
    )

    stats = streamer.stream_missing_history(
        target_user_id="127.0.0.1:5002",
        have_vector_clock={"127.0.0.1:5001": 1},
        transfer_id="recover-abc",
        chunk_size=2,
    )

    assert stats == {
        "transfer_id": "recover-abc",
        "target_user_id": "127.0.0.1:5002",
        "chunks_sent": 2,
        "messages_sent": 4,
    }
    assert len(broadcaster.messages) == 2

    first_payload = json.loads(broadcaster.messages[0].content)
    assert first_payload["type"] == "history_chunk"
    assert first_payload["target_user_id"] == "127.0.0.1:5002"
    assert first_payload["source_user_id"] == "127.0.0.1:5001"
    assert first_payload["chunk_id"] == 1
    assert first_payload["is_last"] is False
    assert [msg["id"] for msg in first_payload["messages"]] == ["msg-002", "msg-003"]

    second_payload = json.loads(broadcaster.messages[1].content)
    assert second_payload["is_last"] is True
    assert [msg["id"] for msg in second_payload["messages"]] == ["msg-004", "msg-005"]


def test_handle_transport_message_saves_chunk_for_self():
    source_store = LocalMessageStore()
    source_store.save(make_message("msg-001", "hello", seq=1))
    source_store.save(make_message("msg-002", "world", seq=2))

    broadcaster = FakeBroadcaster()
    source_streamer = HistoryChunkStreamer(
        store=source_store,
        broadcaster=broadcaster,
        self_user_id="127.0.0.1:5001",
        message_factory=FakeBroadcastMessage,
    )
    source_streamer.stream_missing_history(
        target_user_id="127.0.0.1:5002",
        have_vector_clock={},
        transfer_id="recover-abc",
        chunk_size=10,
    )

    shutil.rmtree(LOG_DIR, ignore_errors=True)
    shutil.rmtree(INDEX_DIR, ignore_errors=True)
    target_store = LocalMessageStore()
    target_streamer = HistoryChunkStreamer(
        store=target_store,
        broadcaster=FakeBroadcaster(),
        self_user_id="127.0.0.1:5002",
        message_factory=FakeBroadcastMessage,
    )

    result = target_streamer.handle_transport_message(broadcaster.messages[0])

    assert result["handled"] is True
    assert result["saved"] == 2
    assert result["duplicates"] == 0
    assert [msg.id for msg in target_store.get_recent()] == ["msg-001", "msg-002"]


def test_handle_transport_message_ignores_chunks_for_other_targets():
    store = LocalMessageStore()
    streamer = HistoryChunkStreamer(
        store=store,
        broadcaster=FakeBroadcaster(),
        self_user_id="127.0.0.1:5003",
        message_factory=FakeBroadcastMessage,
    )
    payload = {
        "type": "history_chunk",
        "transfer_id": "recover-abc",
        "chunk_id": 1,
        "target_user_id": "127.0.0.1:5002",
        "messages": [json.loads(make_message("msg-001", "hello").to_json())],
    }

    result = streamer.handle_transport_message(
        FakeBroadcastMessage(content=json.dumps(payload), sender="127.0.0.1:5001")
    )

    assert result == {"handled": False, "reason": "not_target"}
    assert store.get_recent() == []

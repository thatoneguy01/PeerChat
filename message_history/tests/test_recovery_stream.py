import json
import shutil
from pathlib import Path

import pytest

from message_history.storage import HistoryChunkStreamer, LocalMessageStore, Message
from message_history.storage.recovery_stream import RECOVER_REQUEST


BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
INDEX_DIR = BASE_DIR / "index"
SNAPSHOT_DIR = BASE_DIR / "snapshots"


class FakeBroadcastMessage:
    def __init__(self, content: str, sender: str):
        self.content = content
        self.sender = sender


class FakeBroadcaster:
    def __init__(self):
        self.sent = []

    def send_to_peer(self, host, port, message):
        self.sent.append((host, port, message))


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
    shutil.rmtree(SNAPSHOT_DIR, ignore_errors=True)
    yield
    shutil.rmtree(LOG_DIR, ignore_errors=True)
    shutil.rmtree(INDEX_DIR, ignore_errors=True)
    shutil.rmtree(SNAPSHOT_DIR, ignore_errors=True)


def test_stream_missing_history_sends_direct_chunks():
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
        target_host="127.0.0.1",
        target_port=5002,
        have_vector_clock={"127.0.0.1:5001": 1},
        transfer_id="recover-abc",
        chunk_size=2,
    )

    assert stats == {
        "transfer_id": "recover-abc",
        "target_host": "127.0.0.1",
        "target_port": 5002,
        "chunks_sent": 2,
        "messages_sent": 4,
    }
    assert len(broadcaster.sent) == 2
    assert broadcaster.sent[0][0:2] == ("127.0.0.1", 5002)
    assert broadcaster.sent[1][0:2] == ("127.0.0.1", 5002)

    first_payload = json.loads(broadcaster.sent[0][2].content)
    assert first_payload["type"] == "history_chunk"
    assert first_payload["source_user_id"] == "127.0.0.1:5001"
    assert first_payload["chunk_id"] == 1
    assert first_payload["is_last"] is False
    assert [msg["id"] for msg in first_payload["messages"]] == ["msg-002", "msg-003"]

    second_payload = json.loads(broadcaster.sent[1][2].content)
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
        target_host="127.0.0.1",
        target_port=5002,
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

    result = target_streamer.handle_transport_message(broadcaster.sent[0][2])

    assert result["handled"] is True
    assert result["saved"] == 2
    assert result["duplicates"] == 0
    assert [msg.id for msg in target_store.get_recent()] == ["msg-001", "msg-002"]


def test_send_recover_request_sends_cursor_to_provider():
    store = LocalMessageStore()
    store.save(make_message("msg-001", "hello", seq=1))
    broadcaster = FakeBroadcaster()
    streamer = HistoryChunkStreamer(
        store=store,
        broadcaster=broadcaster,
        self_user_id="127.0.0.1:5002",
        message_factory=FakeBroadcastMessage,
    )

    result = streamer.send_recover_request(
        provider_host="127.0.0.1",
        provider_port=5001,
        requester_host="127.0.0.1",
        requester_port=5002,
        transfer_id="recover-abc",
    )

    assert result["transfer_id"] == "recover-abc"
    assert result["have_vector_clock"] == {"127.0.0.1:5001": 1}
    assert len(broadcaster.sent) == 1
    assert broadcaster.sent[0][0:2] == ("127.0.0.1", 5001)

    payload = json.loads(broadcaster.sent[0][2].content)
    assert payload == {
        "type": RECOVER_REQUEST,
        "transfer_id": "recover-abc",
        "requester_id": "127.0.0.1:5002",
        "requester_host": "127.0.0.1",
        "requester_port": 5002,
        "have_vector_clock": {"127.0.0.1:5001": 1},
    }


def test_handle_recover_request_streams_missing_history_back_to_requester():
    provider_store = LocalMessageStore()
    for i in range(5):
        provider_store.save(make_message(f"msg-{i + 1:03}", f"msg {i + 1}", seq=i + 1))

    provider_broadcaster = FakeBroadcaster()
    provider_streamer = HistoryChunkStreamer(
        store=provider_store,
        broadcaster=provider_broadcaster,
        self_user_id="127.0.0.1:5001",
        message_factory=FakeBroadcastMessage,
    )
    request_payload = {
        "type": RECOVER_REQUEST,
        "transfer_id": "recover-abc",
        "requester_id": "127.0.0.1:5002",
        "requester_host": "127.0.0.1",
        "requester_port": 5002,
        "have_vector_clock": {"127.0.0.1:5001": 2},
    }

    result = provider_streamer.handle_transport_message(
        FakeBroadcastMessage(content=json.dumps(request_payload), sender="127.0.0.1:5002")
    )

    assert result["handled"] is True
    assert result["type"] == RECOVER_REQUEST
    assert result["chunks_sent"] == 1
    assert result["messages_sent"] == 3
    assert provider_broadcaster.sent[0][0:2] == ("127.0.0.1", 5002)

    chunk_payload = json.loads(provider_broadcaster.sent[0][2].content)
    assert chunk_payload["type"] == "history_chunk"
    assert chunk_payload["transfer_id"] == "recover-abc"
    assert [msg["id"] for msg in chunk_payload["messages"]] == [
        "msg-003",
        "msg-004",
        "msg-005",
    ]


def test_handle_recover_request_rejects_invalid_payload():
    store = LocalMessageStore()
    streamer = HistoryChunkStreamer(
        store=store,
        broadcaster=FakeBroadcaster(),
        self_user_id="127.0.0.1:5001",
        message_factory=FakeBroadcastMessage,
    )

    result = streamer.handle_transport_message(
        FakeBroadcastMessage(
            content=json.dumps({"type": RECOVER_REQUEST, "requester_host": "127.0.0.1"}),
            sender="127.0.0.1:5002",
        )
    )

    assert result == {"handled": False, "reason": "invalid_recover_request"}


def test_handle_transport_message_ignores_non_recovery_messages():
    store = LocalMessageStore()
    streamer = HistoryChunkStreamer(
        store=store,
        broadcaster=FakeBroadcaster(),
        self_user_id="127.0.0.1:5003",
        message_factory=FakeBroadcastMessage,
    )
    payload = {
        "type": "chat_message",
        "transfer_id": "recover-abc",
        "chunk_id": 1,
        "messages": [json.loads(make_message("msg-001", "hello").to_json())],
    }

    result = streamer.handle_transport_message(
        FakeBroadcastMessage(content=json.dumps(payload), sender="127.0.0.1:5001")
    )

    assert result == {"handled": False, "reason": "not_recovery_message"}
    assert store.get_recent() == []

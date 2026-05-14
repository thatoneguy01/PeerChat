import json
import shutil
from pathlib import Path

import pytest

from storage import HistoryChunkStreamer, LocalMessageStore, Message
from storage.recovery_fanout import request_missing_history_from_all_peers
from storage.recovery_stream import RECOVER_REQUEST


BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
INDEX_DIR = BASE_DIR / "index"
SNAPSHOT_DIR = BASE_DIR / "snapshots"


class FakeBroadcastMessage:
    def __init__(self, content: str, sender: str):
        self.content = content
        self.sender = sender


class FakeBroadcaster:
    def __init__(self, peers=None):
        self.sent = []
        self.peer_registry = FakePeerRegistry(peers or [])

    def send_to_peer(self, host, port, message):
        self.sent.append((host, port, message))


class FakePeerRegistry:
    def __init__(self, peers):
        self._peers = list(peers)

    def get_peers(self):
        return list(self._peers)


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


def test_request_missing_history_from_all_peers_sends_same_cursor_to_each_peer():
    store = LocalMessageStore()
    store.save(make_message("msg-001", "hello", sender="127.0.0.1:5002", seq=1))
    broadcaster = FakeBroadcaster(peers=[
        ("127.0.0.1", 5001),
        ("127.0.0.1", 5002),
        ("127.0.0.1", 5003),
        ("127.0.0.1", 5003),
    ])
    streamer = HistoryChunkStreamer(
        store=store,
        broadcaster=broadcaster,
        self_user_id="127.0.0.1:5002",
        message_factory=FakeBroadcastMessage,
    )

    result = request_missing_history_from_all_peers(
        streamer=streamer,
        requester_host="127.0.0.1",
        requester_port=5002,
        transfer_id="recover-all",
    )

    assert result["transfer_id"] == "recover-all"
    assert result["peers_requested"] == 2
    assert result["targets"] == [
        {"host": "127.0.0.1", "port": 5001},
        {"host": "127.0.0.1", "port": 5003},
    ]
    assert [sent[0:2] for sent in broadcaster.sent] == [
        ("127.0.0.1", 5001),
        ("127.0.0.1", 5003),
    ]

    for _, _, transport_message in broadcaster.sent:
        payload = json.loads(transport_message.content)
        assert payload == {
            "type": RECOVER_REQUEST,
            "transfer_id": "recover-all",
            "requester_id": "127.0.0.1:5002",
            "requester_host": "127.0.0.1",
            "requester_port": 5002,
            "have_vector_clock": {"127.0.0.1:5002": 1},
        }


def test_request_missing_history_from_explicit_peer_list_skips_bad_and_self_peers():
    store = LocalMessageStore()
    broadcaster = FakeBroadcaster()
    streamer = HistoryChunkStreamer(
        store=store,
        broadcaster=broadcaster,
        self_user_id="127.0.0.1:5002",
        message_factory=FakeBroadcastMessage,
    )

    result = request_missing_history_from_all_peers(
        streamer=streamer,
        requester_host="127.0.0.1",
        requester_port=5002,
        peer_addresses=[
            ("127.0.0.1", 5002),
            ("127.0.0.1", 5001),
            ("127.0.0.1", "5003"),
            ("127.0.0.1", "bad-port"),
        ],
        transfer_id="recover-explicit",
    )

    assert result["peers_requested"] == 2
    assert [sent[0:2] for sent in broadcaster.sent] == [
        ("127.0.0.1", 5001),
        ("127.0.0.1", 5003),
    ]


def test_overlapping_history_chunks_are_deduped_at_target():
    provider_a_store = LocalMessageStore()
    provider_a_store.save(make_message("msg-001", "hello", seq=1))
    provider_a_store.save(make_message("msg-002", "world", seq=2))

    broadcaster_a = FakeBroadcaster()
    provider_a = HistoryChunkStreamer(
        store=provider_a_store,
        broadcaster=broadcaster_a,
        self_user_id="127.0.0.1:5001",
        message_factory=FakeBroadcastMessage,
    )
    provider_a.stream_missing_history(
        target_host="127.0.0.1",
        target_port=5003,
        have_vector_clock={},
        transfer_id="recover-all",
        chunk_size=10,
    )

    shutil.rmtree(LOG_DIR, ignore_errors=True)
    shutil.rmtree(INDEX_DIR, ignore_errors=True)
    provider_b_store = LocalMessageStore()
    provider_b_store.save(make_message("msg-001", "hello", seq=1))
    provider_b_store.save(make_message("msg-002", "world", seq=2))

    broadcaster_b = FakeBroadcaster()
    provider_b = HistoryChunkStreamer(
        store=provider_b_store,
        broadcaster=broadcaster_b,
        self_user_id="127.0.0.1:5002",
        message_factory=FakeBroadcastMessage,
    )
    provider_b.stream_missing_history(
        target_host="127.0.0.1",
        target_port=5003,
        have_vector_clock={},
        transfer_id="recover-all",
        chunk_size=10,
    )

    shutil.rmtree(LOG_DIR, ignore_errors=True)
    shutil.rmtree(INDEX_DIR, ignore_errors=True)
    target_store = LocalMessageStore()
    target = HistoryChunkStreamer(
        store=target_store,
        broadcaster=FakeBroadcaster(),
        self_user_id="127.0.0.1:5003",
        message_factory=FakeBroadcastMessage,
    )

    first = target.handle_transport_message(broadcaster_a.sent[0][2])
    second = target.handle_transport_message(broadcaster_b.sent[0][2])

    assert first["saved"] == 2
    assert first["duplicates"] == 0
    assert second["saved"] == 0
    assert second["duplicates"] == 2
    assert [msg.id for msg in target_store.get_recent()] == ["msg-001", "msg-002"]


def test_target_combines_chunks_from_multiple_partial_history_peers():
    provider_b_store = LocalMessageStore()
    provider_b_store.save(make_message("a-001", "A1", sender="A", seq=1))
    provider_b_store.save(make_message("a-003", "A3", sender="A", seq=3))

    broadcaster_b = FakeBroadcaster()
    provider_b = HistoryChunkStreamer(
        store=provider_b_store,
        broadcaster=broadcaster_b,
        self_user_id="127.0.0.1:5001",
        message_factory=FakeBroadcastMessage,
    )
    provider_b.stream_missing_history(
        target_host="127.0.0.1",
        target_port=5003,
        have_vector_clock={},
        transfer_id="recover-all",
        chunk_size=1,
    )

    shutil.rmtree(LOG_DIR, ignore_errors=True)
    shutil.rmtree(INDEX_DIR, ignore_errors=True)
    provider_c_store = LocalMessageStore()
    provider_c_store.save(make_message("a-002", "A2", sender="A", seq=2))
    provider_c_store.save(make_message("a-004", "A4", sender="A", seq=4))

    broadcaster_c = FakeBroadcaster()
    provider_c = HistoryChunkStreamer(
        store=provider_c_store,
        broadcaster=broadcaster_c,
        self_user_id="127.0.0.1:5002",
        message_factory=FakeBroadcastMessage,
    )
    provider_c.stream_missing_history(
        target_host="127.0.0.1",
        target_port=5003,
        have_vector_clock={},
        transfer_id="recover-all",
        chunk_size=1,
    )

    shutil.rmtree(LOG_DIR, ignore_errors=True)
    shutil.rmtree(INDEX_DIR, ignore_errors=True)
    target_store = LocalMessageStore()
    target = HistoryChunkStreamer(
        store=target_store,
        broadcaster=FakeBroadcaster(),
        self_user_id="127.0.0.1:5003",
        message_factory=FakeBroadcastMessage,
    )

    for _, _, chunk in broadcaster_b.sent + broadcaster_c.sent:
        result = target.handle_transport_message(chunk)
        assert result["handled"] is True

    assert len(broadcaster_b.sent) == 2
    assert len(broadcaster_c.sent) == 2
    assert sorted(msg.id for msg in target_store.get_recent()) == [
        "a-001",
        "a-002",
        "a-003",
        "a-004",
    ]

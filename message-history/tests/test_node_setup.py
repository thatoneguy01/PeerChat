import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict

import pytest

from storage import HistoryChunkStreamer, LocalMessageStore
from storage.node_setup import _make_storage_listener
from storage.recovery_stream import HISTORY_CHUNK, RECOVER_REQUEST


BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
INDEX_DIR = BASE_DIR / "index"
SNAPSHOT_DIR = BASE_DIR / "snapshots"


@dataclass
class FakeTransportMessage:
    id: str
    content: str
    sender: str = "127.0.0.1:5001"
    timestamp: float = 1.0
    signature: str = ""
    ttl: int = 0
    vector_clock: Dict[str, int] = field(default_factory=dict)


class FakeBroadcaster:
    def __init__(self):
        self.sent = []

    def send_to_peer(self, host, port, msg):
        self.sent.append((host, port, msg))


class FakeNode:
    def __init__(self):
        self.synced_vector_clocks = []

    def sync_vector_clock(self, vc):
        self.synced_vector_clocks.append(dict(vc))


@pytest.fixture(autouse=True)
def clean_storage():
    shutil.rmtree(LOG_DIR, ignore_errors=True)
    shutil.rmtree(INDEX_DIR, ignore_errors=True)
    shutil.rmtree(SNAPSHOT_DIR, ignore_errors=True)
    yield
    shutil.rmtree(LOG_DIR, ignore_errors=True)
    shutil.rmtree(INDEX_DIR, ignore_errors=True)
    shutil.rmtree(SNAPSHOT_DIR, ignore_errors=True)


def make_streamer_and_store():
    store = LocalMessageStore()
    streamer = HistoryChunkStreamer(
        store=store,
        broadcaster=FakeBroadcaster(),
        self_user_id="127.0.0.1:5003",
        message_factory=FakeTransportMessage,
    )
    return streamer, store


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_chat_message_is_saved():
    streamer, store = make_streamer_and_store()

    listener = _make_storage_listener(streamer, store)

    msg = FakeTransportMessage(
        id="msg-1",
        content="hello",
        sender="127.0.0.1:5001",
        vector_clock={"127.0.0.1:5001": 1},
    )
    listener(msg)

    assert [m.id for m in store.get_recent()] == ["msg-1"]


def test_recovery_chunk_is_not_saved_as_chat():
    streamer, store = make_streamer_and_store()
    listener = _make_storage_listener(streamer, store)

    chunk_payload = {
        "type": HISTORY_CHUNK,
        "transfer_id": "xfer-1",
        "chunk_id": 1,
        "is_last": True,
        "messages": [],
    }
    chunk_msg = FakeTransportMessage(
        id="chunk-1",
        content=json.dumps(chunk_payload),
        sender="127.0.0.1:5001",
    )
    listener(chunk_msg)

    assert store.get_recent() == []


def test_last_recovery_chunk_syncs_distribution_clock_after_save():
    streamer, store = make_streamer_and_store()
    node = FakeNode()
    listener = _make_storage_listener(streamer, store, node)

    chunk_payload = {
        "type": HISTORY_CHUNK,
        "transfer_id": "xfer-1",
        "chunk_id": 1,
        "is_last": True,
        "messages": [
            {
                "id": "msg-1",
                "content": "recovered",
                "sender": "127.0.0.1:5001",
                "timestamp": 1.0,
                "signature": "",
                "ttl": 10,
                "vector_clock": {"127.0.0.1:5001": 1},
            }
        ],
    }
    listener(FakeTransportMessage(id="chunk-1", content=json.dumps(chunk_payload)))

    assert [m.id for m in store.get_recent()] == ["msg-1"]
    assert node.synced_vector_clocks == [{"127.0.0.1:5001": 1}]


def test_non_last_recovery_chunk_does_not_sync_distribution_clock_yet():
    streamer, store = make_streamer_and_store()
    node = FakeNode()
    listener = _make_storage_listener(streamer, store, node)

    chunk_payload = {
        "type": HISTORY_CHUNK,
        "transfer_id": "xfer-1",
        "chunk_id": 1,
        "is_last": False,
        "messages": [
            {
                "id": "msg-1",
                "content": "recovered",
                "sender": "127.0.0.1:5001",
                "timestamp": 1.0,
                "signature": "",
                "ttl": 10,
                "vector_clock": {"127.0.0.1:5001": 7},
            }
        ],
    }
    listener(FakeTransportMessage(id="chunk-1", content=json.dumps(chunk_payload)))

    assert [m.id for m in store.get_recent()] == ["msg-1"]
    assert node.synced_vector_clocks == []


def test_recover_request_is_not_saved_as_chat():
    streamer, store = make_streamer_and_store()
    listener = _make_storage_listener(streamer, store)

    request_payload = {
        "type": RECOVER_REQUEST,
        "transfer_id": "xfer-1",
        "requester_id": "127.0.0.1:5004",
        "requester_host": "127.0.0.1",
        "requester_port": 5004,
        "have_vector_clock": {},
    }
    request_msg = FakeTransportMessage(
        id="req-1",
        content=json.dumps(request_payload),
        sender="127.0.0.1:5004",
    )
    listener(request_msg)

    assert store.get_recent() == []


def test_storage_listener_handles_chat_without_optional_hooks():
    streamer, store = make_streamer_and_store()
    listener = _make_storage_listener(streamer, store)

    msg = FakeTransportMessage(
        id="msg-X",
        content="solo",
        sender="127.0.0.1:5001",
        vector_clock={"127.0.0.1:5001": 1},
    )
    listener(msg)
    assert [m.id for m in store.get_recent()] == ["msg-X"]

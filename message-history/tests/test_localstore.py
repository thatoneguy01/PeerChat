import json
import shutil
from pathlib import Path

import pytest

from storage import Message, LocalMessageStore

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
INDEX_DIR = BASE_DIR / "index"
ACTIVE_LOG = LOG_DIR / "active.log.jsonl"


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_message(
    msg_id: str,
    content: str,
    sender: str = "127.0.0.1:5001",
    seq: int = 1,
) -> Message:
    """Creating a test Message with a simple vector clock."""
    return Message(
        id=msg_id,
        content=content,
        sender=sender,
        timestamp=1747058342.71,
        signature="",
        ttl=10,
        vector_clock={sender: seq},
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def clean_storage():
    shutil.rmtree(LOG_DIR, ignore_errors=True)
    shutil.rmtree(INDEX_DIR, ignore_errors=True)

    yield


# ── Task 1: Saving messages ───────────────────────────────────────────────────

class TestSaveMessage:

    def test_save_new_message_returns_true(self):
        store = LocalMessageStore()
        msg = make_message("msg-001", "hello")
        assert store.save(msg) is True

    def test_duplicate_message_returns_false(self):
        store = LocalMessageStore()
        msg = make_message("msg-001", "hello")
        store.save(msg)
        assert store.save(msg) is False

    def test_message_written_to_log(self):
        store = LocalMessageStore()
        msg = make_message("msg-001", "hello")
        store.save(msg)

        with ACTIVE_LOG.open("r", encoding="utf-8") as f:
            lines = f.readlines()

        assert len(lines) == 1
        assert "msg-001" in lines[0]

    def test_multiple_messages_written_to_log(self):
        store = LocalMessageStore()
        store.save(make_message("msg-001", "hello", seq=1))
        store.save(make_message("msg-002", "world", seq=2))

        with ACTIVE_LOG.open("r", encoding="utf-8") as f:
            lines = f.readlines()

        assert len(lines) == 2

    def test_saved_message_is_valid_json(self):
        store = LocalMessageStore()
        msg = make_message("msg-001", "hello")
        store.save(msg)

        with ACTIVE_LOG.open("r", encoding="utf-8") as f:
            line = f.readline()

        data = json.loads(line)
        assert data["id"] == "msg-001"
        assert data["content"] == "hello"


# ── Task 2: Local indexes ─────────────────────────────────────────────────────

class TestIndexes:

    def test_message_id_index_updated(self):
        store = LocalMessageStore()
        msg = make_message("msg-001", "hello")
        store.save(msg)

        assert "msg-001" in store._message_ids

    def test_sender_seq_index_updated(self):
        store = LocalMessageStore()
        msg = make_message("msg-001", "hello", sender="127.0.0.1:5001", seq=3)
        store.save(msg)

        assert "127.0.0.1:5001" in store._sender_seq
        assert "3" in store._sender_seq["127.0.0.1:5001"]

    def test_latest_vector_clock_updated(self):
        store = LocalMessageStore()
        msg = make_message("msg-001", "hello", sender="127.0.0.1:5001", seq=5)
        store.save(msg)

        assert store._latest_vc["127.0.0.1:5001"] == 5

    def test_latest_vector_clock_tracks_highest_seq(self):
        store = LocalMessageStore()
        store.save(make_message("msg-001", "hello", sender="127.0.0.1:5001", seq=1))
        store.save(make_message("msg-002", "world", sender="127.0.0.1:5001", seq=5))
        store.save(make_message("msg-003", "again", sender="127.0.0.1:5001", seq=3))

        assert store._latest_vc["127.0.0.1:5001"] == 5

    def test_indexes_persisted_to_disk(self):
        store = LocalMessageStore()
        store.save(make_message("msg-001", "hello"))

        assert (INDEX_DIR / "message_id.index").exists()
        assert (INDEX_DIR / "sender_seq.index").exists()
        assert (INDEX_DIR / "latest_vector_clock.json").exists()

    def test_indexes_reload_on_restart(self):
        store1 = LocalMessageStore()
        store1.save(make_message("msg-001", "hello", seq=3))

        store2 = LocalMessageStore()

        assert "msg-001" in store2._message_ids
        assert store2._latest_vc.get("127.0.0.1:5001") == 3

    def test_duplicate_rejected_after_restart(self):
        store1 = LocalMessageStore()
        store1.save(make_message("msg-001", "hello"))

        store2 = LocalMessageStore()

        assert store2.save(make_message("msg-001", "hello")) is False


# ── get_recent ────────────────────────────────────────────────────────────────

class TestGetRecent:

    def test_get_recent_returns_saved_messages(self):
        store = LocalMessageStore()
        store.save(make_message("msg-001", "hello", seq=1))
        store.save(make_message("msg-002", "world", seq=2))

        messages = store.get_recent(limit=10)

        assert len(messages) == 2

    def test_get_recent_respects_limit(self):
        store = LocalMessageStore()

        for i in range(10):
            store.save(make_message(f"msg-{i:03}", f"msg {i}", seq=i + 1))

        messages = store.get_recent(limit=5)

        assert len(messages) == 5

    def test_get_recent_empty_log(self):
        store = LocalMessageStore()

        assert store.get_recent() == []

    def test_get_recent_returns_message_objects(self):
        store = LocalMessageStore()
        store.save(make_message("msg-001", "hello"))

        messages = store.get_recent()

        assert isinstance(messages[0], Message)
        assert messages[0].id == "msg-001"
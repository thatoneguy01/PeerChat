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


# ── Edge Cases ────────────────────────────────────────────────────────────────

class TestEdgeCases:

    def test_vector_clock_tracks_multiple_senders(self):
        """Each sender's seq should be tracked independently."""
        store = LocalMessageStore()
        store.save(make_message("msg-001", "hi",  sender="127.0.0.1:5001", seq=3))
        store.save(make_message("msg-002", "hey", sender="127.0.0.1:5002", seq=7))

        assert store._latest_vc["127.0.0.1:5001"] == 3
        assert store._latest_vc["127.0.0.1:5002"] == 7

    def test_get_recent_skips_corrupted_lines(self):
        """A corrupted log line should be skipped, not crash get_recent()."""
        store = LocalMessageStore()
        store.save(make_message("msg-001", "hello"))

        with ACTIVE_LOG.open("a") as f:
            f.write("this is not json\n")

        messages = store.get_recent()
        assert len(messages) == 1
        assert messages[0].id == "msg-001"

    def test_save_empty_content_message(self):
        """Empty string content is a valid message and should be stored."""
        store = LocalMessageStore()
        msg = make_message("msg-001", "")
        assert store.save(msg) is True

        messages = store.get_recent()
        assert messages[0].content == ""

    def test_vector_clock_does_not_go_backwards(self):
        """Saving an older seq for the same sender should not lower the cursor."""
        store = LocalMessageStore()
        store.save(make_message("msg-001", "first",  sender="127.0.0.1:5001", seq=10))
        store.save(make_message("msg-002", "second", sender="127.0.0.1:5001", seq=3))

        assert store._latest_vc["127.0.0.1:5001"] == 10

    def test_same_content_different_id_both_stored(self):
        """Two messages with the same content but different IDs are both valid."""
        store = LocalMessageStore()
        store.save(make_message("msg-001", "hello", seq=1))
        store.save(make_message("msg-002", "hello", seq=2))

        messages = store.get_recent()
        assert len(messages) == 2

    def test_get_recent_order_preserved(self):
        """Messages should come back in the order they were written."""
        store = LocalMessageStore()
        store.save(make_message("msg-001", "first",  seq=1))
        store.save(make_message("msg-002", "second", seq=2))
        store.save(make_message("msg-003", "third",  seq=3))

        messages = store.get_recent()
        assert [m.id for m in messages] == ["msg-001", "msg-002", "msg-003"]
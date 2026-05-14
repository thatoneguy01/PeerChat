import json
import shutil
from pathlib import Path

import pytest

from storage import Message, LocalMessageStore

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
INDEX_DIR = BASE_DIR / "index"
SNAPSHOT_DIR = BASE_DIR / "snapshots"
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
    shutil.rmtree(SNAPSHOT_DIR, ignore_errors=True)

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


# ── Duplicate recovery ingestion ─────────────────────────────────────────────

class TestSaveMany:

    def test_save_many_saves_new_messages(self):
        store = LocalMessageStore()

        result = store.save_many([
            make_message("msg-001", "hello", seq=1),
            make_message("msg-002", "world", seq=2),
        ])

        assert result == {"saved": 2, "duplicates": 0, "invalid": 0}
        assert [msg.id for msg in store.get_recent()] == ["msg-001", "msg-002"]

    def test_save_many_skips_duplicates(self):
        store = LocalMessageStore()
        store.save(make_message("msg-001", "hello", seq=1))

        result = store.save_many([
            make_message("msg-001", "hello again", seq=1),
            make_message("msg-002", "world", seq=2),
        ])

        assert result == {"saved": 1, "duplicates": 1, "invalid": 0}
        assert [msg.id for msg in store.get_recent()] == ["msg-001", "msg-002"]

    def test_save_many_chunk_retry_is_idempotent(self):
        store = LocalMessageStore()
        chunk = [
            make_message("msg-001", "hello", seq=1),
            make_message("msg-002", "world", seq=2),
        ]

        first_result = store.save_many(chunk)
        retry_result = store.save_many(chunk)

        assert first_result == {"saved": 2, "duplicates": 0, "invalid": 0}
        assert retry_result == {"saved": 0, "duplicates": 2, "invalid": 0}
        assert len(store.get_recent()) == 2

    def test_save_many_counts_invalid_items(self):
        store = LocalMessageStore()

        result = store.save_many([
            make_message("msg-001", "hello", seq=1),
            {"id": "not-a-message"},
        ])

        assert result == {"saved": 1, "duplicates": 0, "invalid": 1}


# ── Catch-up by vector clock ─────────────────────────────────────────────────

class TestGetMissingSince:

    def test_empty_vector_clock_returns_all_messages(self):
        store = LocalMessageStore()
        store.save(make_message("msg-001", "hello", seq=1))
        store.save(make_message("msg-002", "world", seq=2))

        messages = store.get_missing_since({})

        assert [msg.id for msg in messages] == ["msg-001", "msg-002"]

    def test_up_to_date_vector_clock_returns_no_messages(self):
        store = LocalMessageStore()
        store.save(make_message("msg-001", "hello", seq=1))
        store.save(make_message("msg-002", "world", seq=2))

        messages = store.get_missing_since({"127.0.0.1:5001": 2})

        assert messages == []

    def test_partial_vector_clock_returns_only_missing_messages(self):
        store = LocalMessageStore()
        store.save(make_message("msg-001", "one", seq=1))
        store.save(make_message("msg-002", "two", seq=2))
        store.save(make_message("msg-003", "three", seq=3))

        messages = store.get_missing_since({"127.0.0.1:5001": 1})

        assert [msg.id for msg in messages] == ["msg-002", "msg-003"]

    def test_vector_clock_tracks_multiple_senders_independently(self):
        store = LocalMessageStore()
        store.save(make_message("a-001", "a1", sender="127.0.0.1:5001", seq=1))
        store.save(make_message("b-001", "b1", sender="127.0.0.1:5002", seq=1))
        store.save(make_message("a-002", "a2", sender="127.0.0.1:5001", seq=2))
        store.save(make_message("b-002", "b2", sender="127.0.0.1:5002", seq=2))

        messages = store.get_missing_since({
            "127.0.0.1:5001": 1,
            "127.0.0.1:5002": 0,
        })

        assert [msg.id for msg in messages] == ["b-001", "a-002", "b-002"]

    def test_get_missing_since_preserves_log_order(self):
        store = LocalMessageStore()
        store.save(make_message("a-001", "a1", sender="127.0.0.1:5001", seq=1))
        store.save(make_message("b-001", "b1", sender="127.0.0.1:5002", seq=1))
        store.save(make_message("a-002", "a2", sender="127.0.0.1:5001", seq=2))

        messages = store.get_missing_since({})

        assert [msg.id for msg in messages] == ["a-001", "b-001", "a-002"]

    def test_get_missing_since_handles_bad_cursor_values(self):
        store = LocalMessageStore()
        store.save(make_message("msg-001", "hello", seq=1))

        messages = store.get_missing_since({"127.0.0.1:5001": "not-int"})

        assert [msg.id for msg in messages] == ["msg-001"]


# ── History chunks ───────────────────────────────────────────────────────────

class TestHistoryChunks:

    def test_build_history_chunks_splits_missing_messages(self):
        store = LocalMessageStore()
        for i in range(5):
            store.save(make_message(f"msg-{i + 1:03}", f"msg {i + 1}", seq=i + 1))

        chunks = store.build_history_chunks(
            have_vector_clock={"127.0.0.1:5001": 1},
            transfer_id="recover-abc",
            chunk_size=2,
        )

        assert len(chunks) == 2
        assert chunks[0]["type"] == "history_chunk"
        assert chunks[0]["transfer_id"] == "recover-abc"
        assert chunks[0]["chunk_id"] == 1
        assert chunks[0]["is_snapshot"] is False
        assert chunks[0]["is_last"] is False
        assert [msg["id"] for msg in chunks[0]["messages"]] == ["msg-002", "msg-003"]
        assert chunks[1]["chunk_id"] == 2
        assert chunks[1]["is_last"] is True
        assert [msg["id"] for msg in chunks[1]["messages"]] == ["msg-004", "msg-005"]

    def test_build_history_chunks_returns_empty_when_nothing_missing(self):
        store = LocalMessageStore()
        store.save(make_message("msg-001", "hello", seq=1))

        chunks = store.build_history_chunks(
            have_vector_clock={"127.0.0.1:5001": 1},
            transfer_id="recover-abc",
        )

        assert chunks == []

    def test_build_history_chunks_rejects_invalid_chunk_size(self):
        store = LocalMessageStore()

        with pytest.raises(ValueError):
            store.build_history_chunks({}, "recover-abc", chunk_size=0)


# ── Snapshots ────────────────────────────────────────────────────────────────

class TestSnapshots:

    def test_create_snapshot_writes_metadata_and_compressed_log(self):
        store = LocalMessageStore()
        store.save(make_message("msg-001", "hello", seq=1))
        store.save(make_message("msg-002", "world", seq=2))

        meta = store.create_snapshot(snapshot_id="snapshot-test")

        assert meta is not None
        assert meta["snapshot_id"] == "snapshot-test"
        assert meta["message_count"] == 2
        assert meta["covers_until_vector_clock"] == {"127.0.0.1:5001": 2}
        assert len(meta["checksum"]) == 64
        assert (SNAPSHOT_DIR / "snapshot-test.meta.json").exists()
        assert (SNAPSHOT_DIR / "snapshot-test.jsonl.gz").exists()

    def test_read_snapshot_messages_returns_saved_messages(self):
        store = LocalMessageStore()
        store.save(make_message("msg-001", "hello", seq=1))
        store.save(make_message("msg-002", "world", seq=2))
        store.create_snapshot(snapshot_id="snapshot-test")

        messages = store.read_snapshot_messages("snapshot-test")

        assert [msg.id for msg in messages] == ["msg-001", "msg-002"]

    def test_compacted_snapshot_still_participates_in_missing_recovery(self):
        store = LocalMessageStore()
        store.save(make_message("msg-001", "hello", seq=1))
        store.save(make_message("msg-002", "world", seq=2))
        store.create_snapshot(snapshot_id="snapshot-test", compact=True)
        store.save(make_message("msg-003", "again", seq=3))

        messages = store.get_missing_since({"127.0.0.1:5001": 1})

        assert [msg.id for msg in messages] == ["msg-002", "msg-003"]

    def test_get_recent_reads_across_snapshot_and_active_log(self):
        store = LocalMessageStore()
        store.save(make_message("msg-001", "one", seq=1))
        store.save(make_message("msg-002", "two", seq=2))
        store.create_snapshot(snapshot_id="snapshot-test", compact=True)
        store.save(make_message("msg-003", "three", seq=3))

        messages = store.get_recent(limit=2)

        assert [msg.id for msg in messages] == ["msg-002", "msg-003"]

    def test_auto_snapshot_triggers_after_threshold_active_messages(self):
        store = LocalMessageStore(snapshot_threshold=3)
        store.save(make_message("msg-001", "one", seq=1))
        store.save(make_message("msg-002", "two", seq=2))

        assert store.list_snapshots() == []

        store.save(make_message("msg-003", "three", seq=3))

        snapshots = store.list_snapshots()
        assert len(snapshots) == 1
        assert snapshots[0]["message_count"] == 3
        assert [msg.id for msg in store.read_snapshot_messages("snapshot-0001")] == [
            "msg-001",
            "msg-002",
            "msg-003",
        ]

        with ACTIVE_LOG.open("r", encoding="utf-8") as f:
            assert f.read() == ""

        store.save(make_message("msg-004", "four", seq=4))

        messages = store.get_missing_since({"127.0.0.1:5001": 2})
        assert [msg.id for msg in messages] == ["msg-003", "msg-004"]

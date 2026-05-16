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
    timestamp: float = 1747058342.71,
) -> Message:
    return Message(
        id=msg_id,
        content=content,
        sender=sender,
        timestamp=timestamp,
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

    def test_startup_rebuilds_indexes_when_active_log_is_missing(self):
        """Stale indexes should not claim messages after active log loss."""
        store = LocalMessageStore(snapshot_threshold=None)
        store.save(make_message("msg-001", "hello", seq=1))
        store.save(make_message("msg-002", "world", seq=2))

        ACTIVE_LOG.unlink()

        repaired = LocalMessageStore(snapshot_threshold=None)

        assert repaired.get_recent() == []
        assert repaired.get_latest_vector_clock() == {}

        result = repaired.save_many([
            make_message("msg-001", "hello", seq=1),
            make_message("msg-002", "world", seq=2),
        ])

        assert result == {"saved": 2, "duplicates": 0, "invalid": 0}
        assert [msg.id for msg in repaired.get_recent()] == ["msg-001", "msg-002"]
        assert repaired.get_latest_vector_clock() == {"127.0.0.1:5001": 2}

    def test_corrupt_snapshot_forces_full_recovery_until_gap_is_filled(self):
        """A lost compacted snapshot should lower the recovery cursor safely."""
        store = LocalMessageStore(snapshot_threshold=None)
        store.save(make_message("msg-001", "one", seq=1))
        store.save(make_message("msg-002", "two", seq=2))
        store.create_snapshot(snapshot_id="snapshot-test", compact=True)
        store.save(make_message("msg-003", "three", seq=3))

        (SNAPSHOT_DIR / "snapshot-test.jsonl.gz").write_bytes(b"not a gzip file")

        repaired = LocalMessageStore(snapshot_threshold=None)

        assert [msg.id for msg in repaired.get_recent()] == ["msg-003"]
        assert repaired.get_latest_vector_clock() == {}

        result = repaired.save_many([
            make_message("msg-001", "one", seq=1),
            make_message("msg-002", "two", seq=2),
            make_message("msg-003", "three", seq=3),
        ])

        assert result == {"saved": 2, "duplicates": 1, "invalid": 0}
        assert [msg.id for msg in repaired.get_recent()] == [
            "msg-003",
            "msg-001",
            "msg-002",
        ]
        assert repaired.get_latest_vector_clock() == {"127.0.0.1:5001": 3}

    def test_missing_snapshot_data_forces_full_recovery_until_gap_is_filled(self):
        store = LocalMessageStore(snapshot_threshold=None)
        store.save(make_message("msg-001", "one", seq=1))
        store.save(make_message("msg-002", "two", seq=2))
        store.create_snapshot(snapshot_id="snapshot-test", compact=True)
        store.save(make_message("msg-003", "three", seq=3))

        (SNAPSHOT_DIR / "snapshot-test.jsonl.gz").unlink()

        repaired = LocalMessageStore(snapshot_threshold=None)

        assert [msg.id for msg in repaired.get_recent()] == ["msg-003"]
        assert repaired.get_latest_vector_clock() == {}

    def test_recovered_snapshot_messages_are_read_in_original_time_order(self):
        store = LocalMessageStore(snapshot_threshold=None)
        store.save(make_message("msg-001", "one", seq=1, timestamp=1.0))
        store.save(make_message("msg-002", "two", seq=2, timestamp=2.0))
        store.create_snapshot(snapshot_id="snapshot-test", compact=True)
        store.save(make_message("msg-004", "four", seq=4, timestamp=4.0))

        (SNAPSHOT_DIR / "snapshot-test.jsonl.gz").unlink()
        repaired = LocalMessageStore(snapshot_threshold=None)

        repaired.save_many([
            make_message("msg-001", "one", seq=1, timestamp=1.0),
            make_message("msg-002", "two", seq=2, timestamp=2.0),
            make_message("msg-003", "three", seq=3, timestamp=3.0),
            make_message("msg-004", "four", seq=4, timestamp=4.0),
        ])

        assert [msg.id for msg in repaired.get_recent()] == [
            "msg-001",
            "msg-002",
            "msg-003",
            "msg-004",
        ]

    def test_deleted_middle_snapshots_force_full_recovery_and_accept_missing_messages(self):
        store = LocalMessageStore(snapshot_threshold=3)
        messages = [
            make_message(f"msg-{i:03}", f"msg {i}", seq=i, timestamp=float(i))
            for i in range(1, 17)
        ]
        for msg in messages:
            store.save(msg)

        for snapshot_number in [2, 3, 4]:
            (SNAPSHOT_DIR / f"snapshot-{snapshot_number:04}.jsonl.gz").unlink()
            (SNAPSHOT_DIR / f"snapshot-{snapshot_number:04}.meta.json").unlink()

        repaired = LocalMessageStore(snapshot_threshold=3)

        assert [msg.id for msg in repaired.get_recent()] == [
            "msg-001",
            "msg-002",
            "msg-003",
            "msg-013",
            "msg-014",
            "msg-015",
            "msg-016",
        ]
        assert repaired.get_latest_vector_clock() == {}

        result = repaired.save_many(messages)

        assert result == {"saved": 9, "duplicates": 7, "invalid": 0}
        assert [msg.id for msg in repaired.get_recent()] == [
            f"msg-{i:03}" for i in range(1, 17)
        ]
        assert [
            path.name
            for path in sorted(SNAPSHOT_DIR.glob("*.meta.json"))
        ] == [
            f"snapshot-{i:04}.meta.json"
            for i in range(1, 6)
        ]
        assert [
            path.name
            for path in sorted(SNAPSHOT_DIR.glob("*.jsonl.gz"))
        ] == [
            f"snapshot-{i:04}.jsonl.gz"
            for i in range(1, 6)
        ]
        with ACTIVE_LOG.open("r", encoding="utf-8") as f:
            assert [
                Message.from_json(line).id
                for line in f
            ] == ["msg-016"]
        assert repaired.get_latest_vector_clock() == {"127.0.0.1:5001": 16}

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

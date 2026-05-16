import gzip
import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .models import Message


# ── File paths ────────────────────────────────────────────────────────────────
# This file lives in: message-history/storage/local_message_store.py
# BASE_DIR becomes:   message-history/
BASE_DIR = Path(__file__).resolve().parent.parent

LOG_DIR = BASE_DIR / "logs"
INDEX_DIR = BASE_DIR / "index"
SNAPSHOT_DIR = BASE_DIR / "snapshots"

ACTIVE_LOG = LOG_DIR / "active.log.jsonl"
MSG_ID_INDEX = INDEX_DIR / "message_id.index"
SENDER_INDEX = INDEX_DIR / "sender_seq.index"
VC_INDEX = INDEX_DIR / "latest_vector_clock.json"
RECOVERY_STATE = INDEX_DIR / "recovery_state.json"
DEFAULT_SNAPSHOT_THRESHOLD = 3


class LocalMessageStore:
    """
    Owns all local message storage for this node.

    Responsibilities:
      1. Save incoming messages to the append-only log
      2. Keep in-memory + on-disk indexes up to date
      3. Provide get_recent() so backfill can replay history
    """

    def __init__(self, snapshot_threshold: Optional[int] = DEFAULT_SNAPSHOT_THRESHOLD):
        self._lock = threading.RLock()
        self._snapshot_threshold = snapshot_threshold
        self._ensure_dirs()
        self._load_indexes()
        self._repair_indexes_from_readable_storage()

    # ── Startup ───────────────────────────────────────────────────────────────

    def _ensure_dirs(self):
        """Create runtime storage directories and active log."""
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        ACTIVE_LOG.touch(exist_ok=True)

    def _load_indexes(self):
        """
        Load all three indexes into memory on startup.
        If a file is missing, empty, or corrupt, start fresh.
        """
        message_ids = self._read_json(MSG_ID_INDEX, default=[])
        self._message_ids = set(message_ids) if isinstance(message_ids, list) else set()

        sender_seq = self._read_json(SENDER_INDEX, default={})
        self._sender_seq: Dict[str, Dict[str, int]] = (
            {
                sender: {
                    str(seq): int(offset)
                    for seq, offset in seq_map.items()
                }
                for sender, seq_map in sender_seq.items()
                if isinstance(seq_map, dict)
            }
            if isinstance(sender_seq, dict)
            else {}
        )

        latest_vc = self._read_json(VC_INDEX, default={})
        self._latest_vc: Dict[str, int] = (
            {
                sender: int(seq)
                for sender, seq in latest_vc.items()
            }
            if isinstance(latest_vc, dict)
            else {}
        )

        recovery_state = self._read_json(RECOVERY_STATE, default={})
        self._force_full_recovery_cursor = (
            bool(recovery_state.get("force_full_recovery_cursor"))
            if isinstance(recovery_state, dict)
            else False
        )
        self._needs_snapshot_rebuild = (
            bool(recovery_state.get("needs_snapshot_rebuild"))
            if isinstance(recovery_state, dict)
            else False
        )

    def _repair_indexes_from_readable_storage(self) -> None:
        """
        Reconcile indexes with the message data that is actually readable.

        """
        with self._lock:
            loaded_message_ids = set(self._message_ids)
            self._active_log_had_invalid_lines = False
            storage_damage_detected = (
                self._has_unreadable_snapshot_unlocked()
                or self._has_snapshot_sequence_gaps_unlocked()
            )
            repaired_message_ids: set[str] = set()
            repaired_sender_seq: Dict[str, Dict[str, int]] = {}
            repaired_latest_vc: Dict[str, int] = {}
            changed = False

            for source, msg, offset in self._iter_readable_messages_with_offsets_unlocked():
                if msg.id in repaired_message_ids:
                    changed = True
                    continue

                repaired_message_ids.add(msg.id)
                seq = msg.sender_seq()
                repaired_sender_seq.setdefault(msg.sender, {})[str(seq)] = (
                    offset if source == "active" else -1
                )
                if seq > repaired_latest_vc.get(msg.sender, 0):
                    repaired_latest_vc[msg.sender] = seq

            if (
                repaired_message_ids != self._message_ids
                or repaired_sender_seq != self._sender_seq
                or repaired_latest_vc != self._latest_vc
            ):
                changed = True

            if self._active_log_had_invalid_lines:
                storage_damage_detected = True
            if self._has_sender_sequence_gaps(repaired_sender_seq):
                storage_damage_detected = True

            if changed:
                if (
                    storage_damage_detected
                    or not loaded_message_ids.issubset(repaired_message_ids)
                ):
                    self._force_full_recovery_cursor = True
                    self._needs_snapshot_rebuild = True
                self._message_ids = repaired_message_ids
                self._sender_seq = repaired_sender_seq
                self._latest_vc = repaired_latest_vc
                self._flush_indexes()

            elif storage_damage_detected:
                self._force_full_recovery_cursor = True
                self._needs_snapshot_rebuild = True
                self._flush_indexes()

    def _read_json(self, path: Path, default):
        """
        Read a JSON file from disk.

        Returns default if the file is missing, empty, or invalid.
        """
        if not path.exists() or path.stat().st_size == 0:
            return default

        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return default

    # ── Core: save a message ──────────────────────────────────────────────────

    def save(self, msg: Message) -> bool:
        """
        Persist a new message.

        Returns True if the message was saved.
        Returns False if it was already known.
        """
        with self._lock:
            if msg.id in self._message_ids:
                return False

            offset = self._append_to_log(msg)

            sender = msg.sender
            seq = msg.sender_seq()
            seq_key = str(seq)

            self._message_ids.add(msg.id)

            if sender not in self._sender_seq:
                self._sender_seq[sender] = {}

            self._sender_seq[sender][seq_key] = offset

            if seq > self._latest_vc.get(sender, 0):
                self._latest_vc[sender] = seq

            self._flush_indexes()
            if self._needs_snapshot_rebuild:
                self._maybe_rebuild_snapshots_after_recovery_unlocked()
            else:
                self._maybe_create_snapshot_unlocked()

            return True

    def save_many(self, messages: Iterable[Message]) -> Dict[str, int]:
        """
        Persist a batch of messages, such as one received recovery chunk.

        Duplicate messages are treated as successful no-ops so callers can
        safely retry chunks after a lost ACK.
        """
        result = {
            "saved": 0,
            "duplicates": 0,
            "invalid": 0,
        }

        for msg in messages:
            if not isinstance(msg, Message):
                result["invalid"] += 1
                continue

            try:
                was_saved = self.save(msg)
            except (AttributeError, TypeError, ValueError):
                result["invalid"] += 1
                continue

            if was_saved:
                result["saved"] += 1
            else:
                result["duplicates"] += 1

        with self._lock:
            if self._needs_snapshot_rebuild:
                self._maybe_rebuild_snapshots_after_recovery_unlocked()

        return result

    def has_message(self, message_id: str) -> bool:
        """Return True if this message ID is already stored locally."""
        with self._lock:
            return message_id in self._message_ids

    def _append_to_log(self, msg: Message) -> int:
        """
        Append one JSON line to active.log.jsonl.

        Returns the byte offset where this line starts.
        """
        line = msg.to_json() + "\n"

        with ACTIVE_LOG.open("a", encoding="utf-8") as f:
            offset = f.tell()
            f.write(line)
            f.flush()
            os.fsync(f.fileno())

        return offset

    def _flush_indexes(self):
        """Write all three in-memory indexes back to disk."""
        self._write_json(MSG_ID_INDEX, list(self._message_ids))
        self._write_json(SENDER_INDEX, self._sender_seq)
        self._write_json(VC_INDEX, self._latest_vc)
        self._write_json(
            RECOVERY_STATE,
            {
                "force_full_recovery_cursor": self._force_full_recovery_cursor,
                "needs_snapshot_rebuild": self._needs_snapshot_rebuild,
            },
        )

    def _write_json(self, path: Path, data):
        """Atomically write JSON to a file."""
        tmp = path.with_suffix(path.suffix + ".tmp")

        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())

        tmp.replace(path)

    # ── Read: recent messages for backfill ────────────────────────────────────

    def get_recent(self, limit: int = 100) -> List[Message]:
        """
        Return the last `limit` messages from snapshots plus active log.

        Used by the backfill task to replay history to a new member.
        """
        with self._lock:
            messages = self._read_all_messages_unlocked()
            return messages[-limit:] if len(messages) > limit else messages

    def get_missing_since(self, have_vector_clock: Dict[str, int]) -> List[Message]:
        """
        Return messages this store has that a peer is missing.

        `have_vector_clock` is the peer's recovery cursor. A message is missing
        when its sender sequence is greater than the peer's known sequence for
        that sender. Returned messages preserve active-log order.
        """
        have_vector_clock = have_vector_clock or {}

        with self._lock:
            missing: List[Message] = []
            seen_ids: set[str] = set()

            for msg in self._read_all_messages_unlocked():
                if msg.id in seen_ids:
                    continue
                seen_ids.add(msg.id)

                peer_seq = self._safe_int(have_vector_clock.get(msg.sender, 0))
                if msg.sender_seq() > peer_seq:
                    missing.append(msg)

            return missing

    def build_history_chunks(
        self,
        have_vector_clock: Dict[str, int],
        transfer_id: str,
        chunk_size: int = 100,
    ) -> List[Dict]:
        """
        Build serializable history chunks for direct peer-to-peer recovery.

        """
        if chunk_size <= 0:
            raise ValueError("chunk_size must be greater than 0")

        missing = self.get_missing_since(have_vector_clock)
        chunks: List[Dict] = []

        for start in range(0, len(missing), chunk_size):
            chunk_messages = missing[start:start + chunk_size]
            chunks.append(
                {
                    "type": "history_chunk",
                    "transfer_id": transfer_id,
                    "chunk_id": len(chunks) + 1,
                    "is_snapshot": False,
                    "is_last": start + chunk_size >= len(missing),
                    "messages": [
                        json.loads(msg.to_json())
                        for msg in chunk_messages
                    ],
                }
            )

        return chunks

    def _read_message_at_offset(self, offset: int) -> Optional[Message]:
        """Read one message from active.log.jsonl by byte offset."""
        try:
            with ACTIVE_LOG.open("r", encoding="utf-8") as f:
                f.seek(offset)
                line = f.readline().strip()
        except (OSError, ValueError):
            return None

        if not line:
            return None

        try:
            return Message.from_json(line)
        except (KeyError, json.JSONDecodeError, TypeError, ValueError):
            return None

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def create_snapshot(
        self,
        snapshot_id: Optional[str] = None,
        compact: bool = False,
    ) -> Optional[Dict]:
        """
        Write a compressed snapshot of current active-log messages.

        When compact=True, active.log.jsonl is truncated after the snapshot is
        safely written. Recovery still reads from snapshots plus active log.
        """
        with self._lock:
            messages = self._read_active_messages_unlocked()
            if not messages:
                return None

            snapshot_id = snapshot_id or self._next_snapshot_id_unlocked()
            data_name = f"{snapshot_id}.jsonl.gz"
            meta_name = f"{snapshot_id}.meta.json"
            data_path = SNAPSHOT_DIR / data_name
            meta_path = SNAPSHOT_DIR / meta_name

            payload = "".join(msg.to_json() + "\n" for msg in messages).encode("utf-8")
            meta = self._write_snapshot_files_unlocked(
                snapshot_id=snapshot_id,
                messages=messages,
                data_path=data_path,
                meta_path=meta_path,
                data_name=data_name,
                payload=payload,
            )

            if compact:
                ACTIVE_LOG.write_text("", encoding="utf-8")
                self._rebuild_indexes_unlocked()
                self._flush_indexes()

            return meta

    def _maybe_create_snapshot_unlocked(self) -> None:
        if self._snapshot_threshold is None:
            return
        if self._snapshot_threshold <= 0:
            return
        if self._active_log_message_count_unlocked() >= self._snapshot_threshold:
            self.create_snapshot(compact=True)

    def _maybe_rebuild_snapshots_after_recovery_unlocked(self) -> None:
        if self._has_sender_sequence_gaps_unlocked():
            return
        self._rebuild_snapshots_from_readable_messages_unlocked()

    def _rebuild_snapshots_from_readable_messages_unlocked(self) -> None:
        messages = self._read_all_messages_unlocked()
        if not messages:
            return

        chunk_size = (
            self._snapshot_threshold
            if self._snapshot_threshold is not None and self._snapshot_threshold > 0
            else len(messages)
        )

        snapshot_message_count = len(messages)
        if self._snapshot_threshold is not None and self._snapshot_threshold > 0:
            snapshot_message_count = (len(messages) // chunk_size) * chunk_size

        self._delete_snapshot_files_unlocked()

        for index, start in enumerate(
            range(0, snapshot_message_count, chunk_size),
            start=1,
        ):
            snapshot_id = f"snapshot-{index:04d}"
            data_name = f"{snapshot_id}.jsonl.gz"
            meta_name = f"{snapshot_id}.meta.json"
            data_path = SNAPSHOT_DIR / data_name
            meta_path = SNAPSHOT_DIR / meta_name
            chunk_messages = messages[start:start + chunk_size]
            payload = "".join(
                msg.to_json() + "\n"
                for msg in chunk_messages
            ).encode("utf-8")
            self._write_snapshot_files_unlocked(
                snapshot_id=snapshot_id,
                messages=chunk_messages,
                data_path=data_path,
                meta_path=meta_path,
                data_name=data_name,
                payload=payload,
            )

        self._write_active_log_messages_unlocked(messages[snapshot_message_count:])
        self._needs_snapshot_rebuild = False
        self._force_full_recovery_cursor = False
        self._rebuild_indexes_unlocked()
        self._flush_indexes()

    def _write_snapshot_files_unlocked(
        self,
        snapshot_id: str,
        messages: List[Message],
        data_path: Path,
        meta_path: Path,
        data_name: str,
        payload: bytes,
    ) -> Dict:
        tmp_data_path = data_path.with_suffix(data_path.suffix + ".tmp")
        with gzip.open(tmp_data_path, "wb") as f:
            f.write(payload)
        tmp_data_path.replace(data_path)

        checksum = self._sha256_file(data_path)
        meta = {
            "snapshot_id": snapshot_id,
            "created_at": time.time(),
            "covers_until_vector_clock": self._vector_clock_for_messages(messages),
            "message_count": len(messages),
            "checksum": checksum,
            "data_file": data_name,
        }

        self._write_json(meta_path, meta)
        return meta

    def _delete_snapshot_files_unlocked(self) -> None:
        for pattern in ("*.jsonl.gz", "*.meta.json", "*.tmp"):
            for path in SNAPSHOT_DIR.glob(pattern):
                try:
                    path.unlink()
                except FileNotFoundError:
                    continue

    def _write_active_log_messages_unlocked(self, messages: List[Message]) -> None:
        payload = "".join(msg.to_json() + "\n" for msg in messages)
        with ACTIVE_LOG.open("w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())

    def list_snapshots(self) -> List[Dict]:
        """Return snapshot metadata sorted by snapshot id."""
        with self._lock:
            snapshots: List[Dict] = []
            for meta_path in sorted(SNAPSHOT_DIR.glob("*.meta.json")):
                meta = self._read_json(meta_path, default=None)
                if isinstance(meta, dict):
                    snapshots.append(meta)
            return snapshots

    def read_snapshot_messages(self, snapshot_id: str) -> List[Message]:
        """Read messages from one snapshot after validating its checksum."""
        with self._lock:
            meta_path = SNAPSHOT_DIR / f"{snapshot_id}.meta.json"
            meta = self._read_json(meta_path, default=None)
            if not isinstance(meta, dict):
                return []

            data_path = SNAPSHOT_DIR / str(meta.get("data_file", ""))
            if not data_path.exists():
                return []

            if self._sha256_file(data_path) != meta.get("checksum"):
                return []

            return self._read_snapshot_file_unlocked(data_path)

    def _read_all_messages_unlocked(self) -> List[Message]:
        """Read snapshots followed by active log, dropping duplicate IDs."""
        messages: List[tuple[float, int, Message]] = []
        seen_ids: set[str] = set()

        for read_index, (_, msg, _) in enumerate(self._iter_readable_messages_with_offsets_unlocked()):
            if msg.id not in seen_ids:
                seen_ids.add(msg.id)
                messages.append((msg.timestamp, read_index, msg))

        messages.sort(key=lambda item: (item[0], item[1]))
        return [msg for _, _, msg in messages]

    def _iter_readable_messages_with_offsets_unlocked(self):
        for meta in self.list_snapshots():
            snapshot_id = str(meta.get("snapshot_id", ""))
            for msg in self.read_snapshot_messages(snapshot_id):
                yield "snapshot", msg, -1

        for msg, offset in self._read_active_messages_with_offsets_unlocked():
            yield "active", msg, offset

    def _read_active_messages_unlocked(self) -> List[Message]:
        return [
            msg
            for msg, _ in self._read_active_messages_with_offsets_unlocked()
        ]

    def _read_active_messages_with_offsets_unlocked(self) -> List[tuple[Message, int]]:
        if not ACTIVE_LOG.exists():
            return []

        messages: List[tuple[Message, int]] = []
        try:
            with ACTIVE_LOG.open("r", encoding="utf-8") as f:
                while True:
                    offset = f.tell()
                    line = f.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        messages.append((Message.from_json(line), offset))
                    except (KeyError, json.JSONDecodeError, TypeError, ValueError):
                        self._active_log_had_invalid_lines = True
                        continue
        except (OSError, UnicodeDecodeError):
            self._active_log_had_invalid_lines = True
        return messages

    def _has_unreadable_snapshot_unlocked(self) -> bool:
        for meta in self.list_snapshots():
            expected_count = self._safe_int(meta.get("message_count", 0))
            if expected_count <= 0:
                continue

            snapshot_id = str(meta.get("snapshot_id", ""))
            if len(self.read_snapshot_messages(snapshot_id)) < expected_count:
                return True

        return False

    def _has_snapshot_sequence_gaps_unlocked(self) -> bool:
        snapshot_numbers: List[int] = []
        for meta in self.list_snapshots():
            snapshot_id = str(meta.get("snapshot_id", ""))
            prefix = "snapshot-"
            if not snapshot_id.startswith(prefix):
                continue

            try:
                snapshot_numbers.append(int(snapshot_id[len(prefix):]))
            except ValueError:
                continue

        if not snapshot_numbers:
            return False

        snapshot_numbers.sort()
        expected = list(range(snapshot_numbers[0], snapshot_numbers[-1] + 1))
        return snapshot_numbers != expected

    def _active_log_message_count_unlocked(self) -> int:
        return len(self._read_active_messages_unlocked())

    def _read_snapshot_file_unlocked(self, data_path: Path) -> List[Message]:
        messages: List[Message] = []
        try:
            with gzip.open(data_path, "rt", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        messages.append(Message.from_json(line))
                    except (KeyError, json.JSONDecodeError, TypeError, ValueError):
                        continue
        except OSError:
            return []
        return messages

    def _next_snapshot_id_unlocked(self) -> str:
        max_snapshot_number = 0
        for meta_path in SNAPSHOT_DIR.glob("snapshot-*.meta.json"):
            stem = meta_path.stem
            if stem.endswith(".meta"):
                stem = stem[:-len(".meta")]

            try:
                snapshot_number = int(stem.removeprefix("snapshot-"))
            except ValueError:
                continue

            if snapshot_number > max_snapshot_number:
                max_snapshot_number = snapshot_number

        return f"snapshot-{max_snapshot_number + 1:04d}"

    def _vector_clock_for_messages(self, messages: Iterable[Message]) -> Dict[str, int]:
        vc: Dict[str, int] = {}
        for msg in messages:
            seq = msg.sender_seq()
            if seq > vc.get(msg.sender, 0):
                vc[msg.sender] = seq
        return vc

    def _sha256_file(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    def _rebuild_indexes_unlocked(self) -> None:
        self._message_ids = set()
        self._sender_seq = {}
        self._latest_vc = {}

        for msg in self._read_all_messages_unlocked():
            self._message_ids.add(msg.id)
            seq = msg.sender_seq()
            self._sender_seq.setdefault(msg.sender, {})[str(seq)] = -1
            if seq > self._latest_vc.get(msg.sender, 0):
                self._latest_vc[msg.sender] = seq

    def _safe_int(self, value) -> int:
        """Convert recovery cursor values to int, defaulting bad data to 0."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    # ── Read: vector clock for recovery ───────────────────────────────────────

    def get_latest_vector_clock(self) -> Dict[str, int]:
        """
        Return this node's current recovery cursor.

        Sent in recover_request so a peer knows what this node already has.
        """
        self._repair_indexes_from_readable_storage()
        with self._lock:
            if self._force_full_recovery_cursor:
                if self._has_sender_sequence_gaps_unlocked():
                    return {}
                self._force_full_recovery_cursor = False
                self._flush_indexes()
            return dict(self._latest_vc)

    def _has_sender_sequence_gaps_unlocked(self) -> bool:
        return self._has_sender_sequence_gaps(self._sender_seq)

    def _has_sender_sequence_gaps(self, sender_seq: Dict[str, Dict[str, int]]) -> bool:
        for seq_map in sender_seq.values():
            seqs = sorted(
                self._safe_int(seq)
                for seq in seq_map
                if self._safe_int(seq) > 0
            )
            if not seqs:
                continue
            if seqs != list(range(1, seqs[-1] + 1)):
                return True
        return False

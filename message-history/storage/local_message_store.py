import json
import os
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .models import Message


# ── File paths ────────────────────────────────────────────────────────────────
# This file lives in: message-history/storage/local_message_store.py
# BASE_DIR becomes:   message-history/
BASE_DIR = Path(__file__).resolve().parent.parent

LOG_DIR = BASE_DIR / "logs"
INDEX_DIR = BASE_DIR / "index"

ACTIVE_LOG = LOG_DIR / "active.log.jsonl"
MSG_ID_INDEX = INDEX_DIR / "message_id.index"
SENDER_INDEX = INDEX_DIR / "sender_seq.index"
VC_INDEX = INDEX_DIR / "latest_vector_clock.json"


class LocalMessageStore:
    """
    Owns all local message storage for this node.

    Responsibilities:
      1. Save incoming messages to the append-only log
      2. Keep in-memory + on-disk indexes up to date
      3. Provide get_recent() so backfill can replay history
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._ensure_dirs()
        self._load_indexes()

    # ── Startup ───────────────────────────────────────────────────────────────

    def _ensure_dirs(self):
        """Create message-history/logs, message-history/index, and active log."""
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        INDEX_DIR.mkdir(parents=True, exist_ok=True)
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
        Return the last `limit` messages from the active log.

        Used by the backfill task to replay history to a new member.
        """
        if not ACTIVE_LOG.exists():
            return []

        with self._lock:
            with ACTIVE_LOG.open("r", encoding="utf-8") as f:
                lines = f.readlines()

        recent_lines = lines[-limit:] if len(lines) > limit else lines

        messages: List[Message] = []
        for line in recent_lines:
            line = line.strip()
            if not line:
                continue

            try:
                messages.append(Message.from_json(line))
            except (KeyError, json.JSONDecodeError, TypeError, ValueError):
                continue

        return messages

    def get_missing_since(self, have_vector_clock: Dict[str, int]) -> List[Message]:
        """
        Return messages this store has that a peer is missing.

        `have_vector_clock` is the peer's recovery cursor. A message is missing
        when its sender sequence is greater than the peer's known sequence for
        that sender. Returned messages preserve active-log order.
        """
        have_vector_clock = have_vector_clock or {}

        with self._lock:
            offsets: List[int] = []

            for sender, seq_map in self._sender_seq.items():
                peer_seq = self._safe_int(have_vector_clock.get(sender, 0))

                for seq_key, offset in seq_map.items():
                    sender_seq = self._safe_int(seq_key)
                    if sender_seq > peer_seq:
                        offsets.append(int(offset))

            messages: List[Message] = []
            for offset in sorted(set(offsets)):
                msg = self._read_message_at_offset(offset)
                if msg is not None:
                    messages.append(msg)

            return messages

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
        with self._lock:
            return dict(self._latest_vc)
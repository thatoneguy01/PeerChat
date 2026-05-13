import json
import logging
import os
import re
from typing import Any

from .event_log import MembershipEventLog
from .models import MembershipEvent
from .snapshot import MembershipSnapshot

logger = logging.getLogger(__name__)

_CHECKPOINT_RE = re.compile(r"^checkpoint_(.+)_(\d+)\.json$")
_KEEP_LAST = 2


class DurabilityManager:
    """Snapshot + log-tail persistence for crash recovery.

    Writes a single JSON file per checkpoint, atomically via tmp + os.replace.
    Keeps the last `_KEEP_LAST` checkpoints per room so recovery can fall back
    to the previous one if the newest is corrupt.
    """

    def __init__(self, storage_dir: str, snapshot_interval: int = 100):
        self._storage_dir = storage_dir
        self._snapshot_interval = snapshot_interval
        os.makedirs(storage_dir, exist_ok=True)

    @property
    def snapshot_interval(self) -> int:
        return self._snapshot_interval

    def maybe_checkpoint(
        self, log: MembershipEventLog, snapshot: MembershipSnapshot
    ) -> bool:
        latest = log.get_latest_seq_no()
        if latest > 0 and latest % self._snapshot_interval == 0:
            self._write_checkpoint(log, snapshot)
            return True
        return False

    def force_checkpoint(
        self, log: MembershipEventLog, snapshot: MembershipSnapshot
    ) -> None:
        self._write_checkpoint(log, snapshot)

    def _write_checkpoint(
        self, log: MembershipEventLog, snapshot: MembershipSnapshot
    ) -> None:
        room_id = log.room_id
        latest = log.get_latest_seq_no()
        payload = {
            "room_id": room_id,
            "as_of_seq_no": snapshot.as_of_seq_no,
            "snapshot": snapshot.serialize(),
            "log_tail": log.serialize_since(snapshot.as_of_seq_no),
            "current_term": log.get_current_term(),
            "next_seq_no": latest + 1,
        }

        filename = f"checkpoint_{room_id}_{latest}.json"
        final_path = os.path.join(self._storage_dir, filename)
        tmp_path = final_path + ".tmp"

        with open(tmp_path, "w") as f:
            json.dump(payload, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, final_path)

        self._prune(room_id)

    def _list_checkpoints(self, room_id: str) -> list[tuple[int, str]]:
        """Return [(seq_no, full_path), ...] sorted by seq_no descending."""
        results: list[tuple[int, str]] = []
        try:
            entries = os.listdir(self._storage_dir)
        except FileNotFoundError:
            return []
        for name in entries:
            m = _CHECKPOINT_RE.match(name)
            if not m:
                continue
            file_room, seq_str = m.group(1), m.group(2)
            if file_room != room_id:
                continue
            results.append((int(seq_str), os.path.join(self._storage_dir, name)))
        results.sort(key=lambda t: t[0], reverse=True)
        return results

    def _prune(self, room_id: str) -> None:
        checkpoints = self._list_checkpoints(room_id)
        for _, path in checkpoints[_KEEP_LAST:]:
            try:
                os.remove(path)
            except OSError as e:
                logger.warning("Failed to prune checkpoint %s: %s", path, e)

    def recover(
        self, room_id: str
    ) -> tuple[MembershipEventLog, MembershipSnapshot] | None:
        for _, path in self._list_checkpoints(room_id):
            try:
                with open(path, "r") as f:
                    payload = json.load(f)
                log, snap = self._reconstruct(room_id, payload)
                return log, snap
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.warning(
                    "Checkpoint %s is corrupt (%s); trying older fallback", path, e
                )
                continue
        return None

    def _reconstruct(
        self, room_id: str, payload: dict[str, Any]
    ) -> tuple[MembershipEventLog, MembershipSnapshot]:
        snap = MembershipSnapshot.deserialize(payload["snapshot"])

        tail_events = [MembershipEvent.from_dict(d) for d in payload["log_tail"]]
        tail_events.sort(key=lambda e: e.seq_no)

        log = MembershipEventLog(room_id)
        log._log = list(tail_events)
        log._next_seq_no = payload["next_seq_no"]
        log._current_term = payload["current_term"]

        # Defensive replay: in case log_tail contained events past snapshot.as_of_seq_no
        for e in tail_events:
            if e.seq_no > snap.as_of_seq_no:
                snap.apply_event(e)

        return log, snap

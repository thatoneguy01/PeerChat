import shutil
from pathlib import Path
from typing import Optional

from . import local_message_store as store_paths
from .node_setup import NodeWiring, wire_node
from .recovery_fanout import request_missing_history_from_all_peers


def configure_storage_root(root: Path | str, *, clean: bool = False) -> Path:
    root = Path(root)
    if clean:
        shutil.rmtree(root, ignore_errors=True)

    store_paths.LOG_DIR = root / "logs"
    store_paths.INDEX_DIR = root / "index"
    store_paths.SNAPSHOT_DIR = root / "snapshots"
    store_paths.ACTIVE_LOG = store_paths.LOG_DIR / "active.log.jsonl"
    store_paths.MSG_ID_INDEX = store_paths.INDEX_DIR / "message_id.index"
    store_paths.SENDER_INDEX = store_paths.INDEX_DIR / "sender_seq.index"
    store_paths.VC_INDEX = store_paths.INDEX_DIR / "latest_vector_clock.json"
    store_paths.RECOVERY_STATE = store_paths.INDEX_DIR / "recovery_state.json"
    return root


class HistoryService:
    """
    Public service wrapper for History/Recovery integration.

    It owns storage wiring, recovery handling, and listener fan-out.
    """

    def __init__(
        self,
        node,
        host: str,
        port: int,
        *,
        storage_root: Optional[Path | str] = None,
        clean_storage: bool = False,
        pull_recovery_on_start: bool = True,
    ) -> None:
        self.node = node
        self.host = host
        self.port = port
        self.storage_root = Path(storage_root) if storage_root is not None else None
        self.clean_storage = clean_storage
        self.pull_recovery_on_start = pull_recovery_on_start
        self._wiring: Optional[NodeWiring] = None

    def start(self) -> NodeWiring:
        if self._wiring is not None:
            return self._wiring

        if self.storage_root is not None:
            configure_storage_root(self.storage_root, clean=self.clean_storage)

        self._wiring = wire_node(
            node=self.node,
            host=self.host,
            port=self.port,
            pull_recovery_on_start=self.pull_recovery_on_start,
        )
        return self._wiring

    def request_missing_history(
        self,
        *,
        peer_addresses=None,
        transfer_id: Optional[str] = None,
    ) -> dict:
        """Ask all active peers for messages this node is missing."""
        wiring = self._require_started()
        return request_missing_history_from_all_peers(
            streamer=wiring.streamer,
            requester_host=self.host,
            requester_port=self.port,
            peer_addresses=peer_addresses,
            transfer_id=transfer_id,
        )

    def _require_started(self) -> NodeWiring:
        if self._wiring is None:
            raise RuntimeError("HistoryService.start() must be called first")
        return self._wiring

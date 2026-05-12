import time
from collections import OrderedDict


class DuplicateGuard:
    """Sliding-window dedup filter.

    Records (user_id, event_type) pairs recently seen. Used by the Coordinator
    to suppress double-submitted requests (e.g., a client rapidly double-clicking
    Join produces two JOIN_REQUESTED events).
    """

    def __init__(self, window_size: int = 100):
        self._recent: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._window_size = window_size

    def is_duplicate(self, user_id: str, event_type: str) -> bool:
        key = (user_id, event_type)
        if key in self._recent:
            return True
        self._recent[key] = time.time()
        if len(self._recent) > self._window_size:
            self._recent.popitem(last=False)
        return False

    def clear(self) -> None:
        self._recent.clear()

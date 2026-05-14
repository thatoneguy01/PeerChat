import logging
from typing import Any, Callable, List

logger = logging.getLogger(__name__)


class Listeners:
    """Fan-out from one on_message slot to multiple subscribers."""

    def __init__(self) -> None:
        self._fns: List[Callable[[Any], None]] = []

    def register(self, fn: Callable[[Any], None]) -> None:
        self._fns.append(fn)

    def dispatch(self, msg: Any) -> None:
        for fn in self._fns:
            try:
                fn(msg)
            except Exception as exc:
                logger.warning("Listener %r raised: %s", fn, exc)

    def __len__(self) -> int:
        return len(self._fns)

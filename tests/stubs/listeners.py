"""
Listener fan-out shim so multiple teams can subscribe to on_message.

Recommended pattern in docs/contract_history.md — UI, Storage, and any other
consumer register callbacks that all fire per delivered message.
"""

from typing import Callable, List

from distribution import Message


class Listeners:
    def __init__(self) -> None:
        self._fns: List[Callable[[Message], None]] = []

    def register(self, fn: Callable[[Message], None]) -> None:
        self._fns.append(fn)

    def dispatch(self, msg: Message) -> None:
        for fn in self._fns:
            try:
                fn(msg)
            except Exception:
                pass

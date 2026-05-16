"""
Stub for the History / Recovery & Storage team.

Implements the listener-append contract from docs/contract_history.md.
Real History replaces this with durable storage.
"""

from threading import Lock
from distribution import Message


class FakeStorage:
    def __init__(self) -> None:
        self._messages: list[Message] = []
        self._lock = Lock()

    def append(self, msg: Message) -> None:
        with self._lock:
            self._messages.append(msg)

    @property
    def messages(self) -> list[Message]:
        with self._lock:
            return list(self._messages)

    def __len__(self) -> int:
        return len(self.messages)

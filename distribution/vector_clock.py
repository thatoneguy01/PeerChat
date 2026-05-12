from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .message import Message


class VectorClock:
    def __init__(self) -> None:
        self._clock: dict[str, int] = {}

    def increment(self, node_id: str) -> None:
        self._clock[node_id] = self._clock.get(node_id, 0) + 1

    def merge(self, other: dict[str, int]) -> None:
        for node, count in other.items():
            self._clock[node] = max(self._clock.get(node, 0), count)

    def snapshot(self) -> dict[str, int]:
        return dict(self._clock)

    def is_ready(self, msg: Message) -> bool:
        vc = msg.vector_clock
        if not vc:
            return True
        sender = msg.sender
        if vc.get(sender, 0) != self._clock.get(sender, 0) + 1:
            return False
        return all(
            count <= self._clock.get(node, 0)
            for node, count in vc.items()
            if node != sender
        )


class HoldBackQueue:
    def __init__(self) -> None:
        self._queue: list[Message] = []

    def add(self, msg: Message) -> None:
        self._queue.append(msg)

    def drain(self, vc: VectorClock) -> list[Message]:
        """
        Release all messages from the queue that are now causally ready given vc.
        Updates vc in place as each message is released so cascading unblocks work.
        Returns released messages in delivery order.
        """
        delivered: list[Message] = []
        changed = True
        while changed:
            changed = False
            remaining: list[Message] = []
            for msg in self._queue:
                if vc.is_ready(msg):
                    vc.merge(msg.vector_clock)
                    delivered.append(msg)
                    changed = True
                else:
                    remaining.append(msg)
            self._queue = remaining
        return delivered

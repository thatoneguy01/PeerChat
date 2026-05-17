from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .message import Message

logger = logging.getLogger(__name__)

HOLDBACK_TIMEOUT = 30.0  # seconds before a stuck message is delivered out of causal order


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
    def __init__(self, timeout: float = HOLDBACK_TIMEOUT) -> None:
        self._queue: list[tuple[float, Message]] = []  # (enqueue_time, msg)
        self._timeout = timeout

    def add(self, msg: Message) -> None:
        self._queue.append((time.monotonic(), msg))

    def drain(self, vc: VectorClock) -> list[Message]:
        """
        Release all messages from the queue that are now causally ready given vc.
        Updates vc in place as each message is released so cascading unblocks work.
        Returns released messages in delivery order.

        Messages that have been waiting longer than HOLDBACK_TIMEOUT are delivered
        out of causal order with a warning rather than held back indefinitely.
        """
        delivered: list[Message] = []
        now = time.monotonic()

        # Flush messages that exceeded the timeout first so their VC entries
        # are merged before the causal drain runs below.
        still_waiting: list[tuple[float, Message]] = []
        for enqueued_at, msg in self._queue:
            if now - enqueued_at > self._timeout:
                logger.warning(
                    "hold-back timeout: delivering %s out of causal order "
                    "(waited %.1fs, predecessor never arrived)",
                    msg.id[:8], now - enqueued_at,
                )
                vc.merge(msg.vector_clock)
                delivered.append(msg)
            else:
                still_waiting.append((enqueued_at, msg))
        self._queue = still_waiting

        changed = True
        while changed:
            changed = False
            remaining: list[tuple[float, Message]] = []
            for enqueued_at, msg in self._queue:
                if vc.is_ready(msg):
                    vc.merge(msg.vector_clock)
                    delivered.append(msg)
                    changed = True
                else:
                    remaining.append((enqueued_at, msg))
            self._queue = remaining
        return delivered

"""
EventNotifier (ZooKeeper-style watch/subscribe)
"""

import logging
from uuid import uuid4
from peer_discovery.membership.models import (
    MembershipEvent, MembershipDelta, SubscriptionHandle
)
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)

@dataclass
class _SubscriptionEntry:
    callback: Callable
    from_version: int

class EventNotifier:
    def __init__(self):
        self._subscribers: dict[str, _SubscriptionEntry] = {}

    def subscribe(self, callback, from_version: int = 0) -> SubscriptionHandle:
        handle = SubscriptionHandle(id=uuid4().hex)
        self._subscribers[handle.id] = _SubscriptionEntry(
            callback=callback,
            from_version=from_version,
        )
        return handle

    def unsubscribe(self, handle: SubscriptionHandle) -> None:
        self._subscribers.pop(handle.id, None)

    def dispatch(self, event: MembershipEvent, delta: MembershipDelta) -> None:
        # Synchronous dispatch; one slow callback can block others
        if delta is None:
            return
        for entry in list(self._subscribers.values()):
            try:
                entry.callback(event, delta)
            except Exception as e:
                logger.warning(f"Notifier callback failed: {e}")

    def deliver_catchup(self, handle: SubscriptionHandle,
                        events_and_deltas: list[tuple[MembershipEvent, MembershipDelta]]) -> None:
        entry = self._subscribers.get(handle.id)
        if not entry:
            return
        for event, delta in events_and_deltas:
            try:
                entry.callback(event, delta)
            except Exception as e:
                logger.warning(f"Notifier catchup callback failed: {e}")

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

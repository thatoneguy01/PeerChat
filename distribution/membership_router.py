"""
Membership-aware routing table for message distribution.

Integrates with the Membership Service to maintain a cached peer list that is:
  - Built once from get_membership_snapshot() at startup
  - Updated in real time via subscribe_membership_events()
  - Filtered to return only ACTIVE peers (holds BACKFILLING peers until they're ready)

Implements PeerRegistry so it's a drop-in replacement for InMemoryRegistry.
No changes to BroadcastNode are needed.
"""

import threading
import logging
from typing import List, Tuple, Set, Optional

from .peer_registry import PeerRegistry

logger = logging.getLogger(__name__)


def _parse_address(user_id: str) -> Tuple[str, int]:
    """
    Parse 'host:port' format user_id into (host, port) tuple.

    Example: '127.0.0.1:5001' -> ('127.0.0.1', 5001)
    """
    host, port_str = user_id.rsplit(":", 1)
    return (host, int(port_str))


class MembershipRouter(PeerRegistry):
    """
    A PeerRegistry that maintains a routing table synchronized with the Membership Service.

    Peers in ACTIVE state are returned by get_peers(). Peers in BACKFILLING or SUSPECTED
    state are held in an internal buffer and promoted to ACTIVE once their state changes.

    This is a drop-in replacement for InMemoryRegistry that provides membership-aware filtering.
    """

    def __init__(self, service, self_address: str) -> None:
        """
        Initialize the router and synchronize with the Membership Service.

        Parameters:
            service: The MembershipService instance to subscribe to
            self_address: This node's address as "host:port" to exclude from routing
        """
        self.service = service
        self.self_address = self_address

        # Active peers: user_id -> (host, port)
        # These peers are ACTIVE and will receive real-time messages
        self._active: dict[str, Tuple[str, int]] = {}

        # Held peers: set of user_ids
        # These peers are BACKFILLING or SUSPECTED and don't receive real-time yet
        self._hold: Set[str] = set()

        self._lock = threading.Lock()
        self._subscription_handle: Optional[object] = None

        # Populate from current snapshot and subscribe to future events
        self._initialize_from_membership()

    def _initialize_from_membership(self) -> None:
        """Load initial state from snapshot and subscribe to events."""
        snapshot = self.service.get_membership_snapshot()

        # Populate active and held peers based on current state
        for user_id, member in snapshot.members.items():
            try:
                addr = _parse_address(user_id)
            except (ValueError, IndexError) as e:
                logger.warning("Could not parse address from user_id %s: %s", user_id, e)
                continue

            # Exclude self
            if user_id == self.self_address:
                continue

            state_name = member.state.name if hasattr(member.state, 'name') else str(member.state)

            if state_name == 'ACTIVE':
                with self._lock:
                    self._active[user_id] = addr
            elif state_name in ('BACKFILLING', 'SUSPECTED'):
                with self._lock:
                    self._hold.add(user_id)
            # DISCONNECTED, LEFT, LEAVING: don't add to either set

        logger.info(
            "MembershipRouter initialized: %d active, %d held",
            len(self._active),
            len(self._hold),
        )

        # Subscribe to future changes (from_version avoids missing events between snapshot and sub)
        self._subscription_handle = self.service.subscribe_membership_events(
            self._on_membership_event,
            from_version=snapshot.version,
        )

    def get_peers(self) -> List[Tuple[str, int]]:
        """
        Return the list of active peers (only ACTIVE members).

        This is called by BroadcastNode._peers_excluding() on every forward.
        Returns a snapshot of currently active peers.
        """
        with self._lock:
            return list(self._active.values())

    def _on_membership_event(self, event, delta=None) -> None:
        """
        Handle a membership event. Updates routing table based on event type.

        This callback fires from the Membership Service's event stream thread.
        All accesses to _active and _hold must be under _lock.
        """
        event_type_name = event.event_type.name if hasattr(event.event_type, 'name') else str(event.event_type)
        user_id = event.user_id

        try:
            if event_type_name == 'JOIN_ACCEPTED':
                self._handle_join_accepted(event)
            elif event_type_name == 'HISTORY_BACKFILL_COMPLETE':
                self._handle_backfill_complete(event)
            elif event_type_name == 'DISCONNECT_SUSPECTED':
                self._handle_disconnect_suspected(event)
            elif event_type_name == 'RECONNECTED':
                self._handle_reconnected(event)
            elif event_type_name == 'LEAVE_CONFIRMED':
                self._handle_leave_confirmed(event)
            elif event_type_name == 'DISCONNECT_TIMEOUT':
                self._handle_disconnect_timeout(event)
            # Other events: ignore (JOIN_REQUESTED, JOIN_REJECTED, LEAVE_REQUESTED, HEARTBEAT, etc.)
        except Exception as exc:
            logger.error("Error handling membership event %s for %s: %s", event_type_name, user_id, exc)

    def _handle_join_accepted(self, event) -> None:
        """Member joined. Add to hold (don't deliver until backfill completes)."""
        user_id = event.user_id

        # Skip self
        if user_id == self.self_address:
            return

        try:
            addr = _parse_address(user_id)
        except (ValueError, IndexError) as e:
            logger.warning("Could not parse address from user_id %s: %s", user_id, e)
            return

        with self._lock:
            # Remove from active if present (shouldn't be, but be safe)
            self._active.pop(user_id, None)
            # Add to hold
            self._hold.add(user_id)
            logger.debug("Member %s joined, added to hold", user_id)

    def _handle_backfill_complete(self, event) -> None:
        """Backfill done. Move from hold to active (start delivering)."""
        user_id = event.user_id

        with self._lock:
            if user_id in self._hold:
                self._hold.remove(user_id)
                # Need to get the address. We should have cached it, but let's be safe.
                try:
                    addr = _parse_address(user_id)
                    self._active[user_id] = addr
                    logger.debug("Member %s backfill complete, moved to active", user_id)
                except (ValueError, IndexError) as e:
                    logger.warning("Could not parse address for %s during backfill complete: %s", user_id, e)

    def _handle_disconnect_suspected(self, event) -> None:
        """Member suspected offline. Move from active to hold (buffer instead of deliver)."""
        user_id = event.user_id

        with self._lock:
            if user_id in self._active:
                del self._active[user_id]
                self._hold.add(user_id)
                logger.debug("Member %s suspected offline, moved to hold", user_id)

    def _handle_reconnected(self, event) -> None:
        """Member reconnected. Move from hold to active (resume delivery)."""
        user_id = event.user_id

        with self._lock:
            if user_id in self._hold:
                self._hold.remove(user_id)
                try:
                    addr = _parse_address(user_id)
                    self._active[user_id] = addr
                    logger.debug("Member %s reconnected, moved to active", user_id)
                except (ValueError, IndexError) as e:
                    logger.warning("Could not parse address for %s during reconnect: %s", user_id, e)

    def _handle_leave_confirmed(self, event) -> None:
        """Member left. Remove from both active and hold."""
        user_id = event.user_id

        with self._lock:
            self._active.pop(user_id, None)
            self._hold.discard(user_id)
            logger.debug("Member %s left, removed from routing", user_id)

    def _handle_disconnect_timeout(self, event) -> None:
        """Member confirmed offline. Remove from both active and hold."""
        user_id = event.user_id

        with self._lock:
            self._active.pop(user_id, None)
            self._hold.discard(user_id)
            logger.debug("Member %s confirmed offline, removed from routing", user_id)

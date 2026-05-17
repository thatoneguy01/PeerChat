from __future__ import annotations

import logging
import tempfile
from typing import TYPE_CHECKING, Callable
from time import time
from flask import current_app, has_app_context
from .contracts import MessageRecord, UserRecord
from distribution import Message
from distribution.peer_registry import PeerRegistry, InMemoryRegistry
from peer_discovery.membership_integration.service import MembershipService
from peer_discovery.network.config import DiscoveryConfig
from peer_discovery.network.discovery_node import DiscoveryNode
from peer_discovery.network.net_utils import get_lan_ip, pick_free_port
from peer_discovery.membership.models import EventType
from utils import get_external_ip

logger = logging.getLogger(__name__)


class Service:
    _BASE_MESSAGES: list[MessageRecord] = []

    def __init__(self, refreshes: dict[str, callable]) -> None:
        self._messages: list[MessageRecord] = self._BASE_MESSAGES
        self._users: list[UserRecord] = []
        self._refreshes = refreshes
        self.message_out = lambda content: None
        self.message_in = lambda content, timestamp, sender_ip: None
        self.discover_node = None
        self.discover_service = None
        self.peer_registry = InMemoryRegistry()
        self.history_service = None
        # Port Distribution's BroadcastNode listens on. main.py overrides
        # peer_registry; if it also changes the BroadcastNode port, it should
        # set chat_service.chat_port to match. Default matches main.py:29.
        self.chat_port = 5678
        # The Security module's public key PEM bytes. Set by main.py after
        # initializing the key store. Passed to DiscoveryConfig so JOIN events
        # advertise the same key the BroadcastNode signs messages with.
        self.public_key_pem: bytes | None = None
        # Captured during connect() so background threads (membership
        # subscriber, BroadcastNode receive task, heartbeats) can push a
        # Flask app context before calling refresh callbacks that need it.
        self._flask_app = None

    def _refresh(self, key: str, payload) -> None:
        """Invoke a refresh callback safely from any thread.

        Wraps the call in a Flask app context when ``self._flask_app`` is
        set, so callbacks that use ``render_template`` / ``url_for`` don't
        crash with "Working outside of application context" when invoked
        from background threads.
        """
        cb = self._refreshes.get(key)
        if cb is None:
            return
        try:
            if self._flask_app is not None:
                with self._flask_app.app_context():
                    cb(payload)
            else:
                cb(payload)
        except Exception as e:
            logger.warning("Refresh callback for %s failed: %s", key, e)

    def get_users(self) -> list[UserRecord]:
        return self._users

    def get_messages(self) -> list[MessageRecord]:
        return self._messages

    def post_message(self, content: str) -> None:
        # self._messages.append({"timestamp":time(), "content": content})
        self.message_out(content)

    def use_history(self, history_service) -> None:
        self.history_service = history_service

    def message_received(self, msg: Message) -> None:
        if self.history_service is not None and self.history_service.handle_message(msg).get("handled"):
            return
        self._messages.append({"sender": msg.sender, "timestamp": msg.timestamp, "content": msg.content})
        self._refresh("messages", self._messages)

    def user_connected(self, username: str, ip: str = "0.0.0.0") -> None:
        if not any(u.get("name") == username for u in self._users):
            self._users.append({"name": username, "status": "Online", "ip": ip})
        self._refresh("users", self._users)

    def user_disconnected(self, username: str, ip: str = "0.0.0.0") -> None:
        self._users[:] = [u for u in self._users if u.get("name") != username]
        self._refresh("users", self._users)

    def connect(self, username: str, ip: str) -> None:
        if username and not any(u.get("name") == username for u in self._users):
            self._users.append({"name": username, "status": "Online", "ip": ip if ip else "0.0.0.0"})

        lan_ip = get_lan_ip()
        listen_port = pick_free_port()
        advertise_address = f"{lan_ip}:{listen_port}"

        # Use a fresh on-disk storage dir per connect so stale checkpoints
        # from prior test runs don't get recovered and confuse the state
        # machine ("Invalid transition" warnings on re-join).
        storage_dir = tempfile.mkdtemp(prefix="peerchat_")

        if ip == "":
            config = DiscoveryConfig(
                advertise_address=advertise_address,
                listen_port=listen_port,
                bootstrap_timeout=5.0,
                public_key_override=self.public_key_pem,
            )
        else:
            # Accept either "host" or "host:port" for the seed. If only a
            # host is given, default to port 8001 (the seed convention).
            seed = ip if ":" in ip else f"{ip}:8001"
            config = DiscoveryConfig(
                advertise_address=advertise_address,
                listen_port=listen_port,
                bootstrap_peers=[seed],
                bootstrap_timeout=5.0,
                public_key_override=self.public_key_pem,
            )

        self.discover_node = DiscoveryNode(
            room_id="default", config=config, storage_dir=storage_dir
        )
        self.discover_service = self.discover_node.service

        # Capture the Flask app while we're still inside the request context.
        # Membership subscriber, BroadcastNode receive task, and heartbeat
        # threads all fire from non-request threads. Without an app context
        # pushed, any callback that calls render_template / url_for / g
        # raises "Working outside of application context".
        if has_app_context():
            self._flask_app = current_app._get_current_object()

        def handle_membership_event(event, delta):
            try:
                if event.event_type == EventType.JOIN_ACCEPTED:
                    host, _disc_port = event.user_id.rsplit(":", 1)
                    # Distribution's BroadcastNode listens on self.chat_port,
                    # NOT on the discovery TCP port encoded in event.user_id.
                    # Sending chat to the discovery port produces the
                    # "Incoming frame size 1195725856" (= ASCII "GET ") errors.
                    self.peer_registry.add_peer(host, self.chat_port, event.public_key or b"")
                    
                    if self.history_service is not None:
                        # History recovery goes through Distribution's BroadcastNode
                        # (send_to_peer), which targets the CHAT port — not the
                        # discovery port. Sending recovery to the discovery port
                        # produces "protocol_error: Missing required fields" warnings
                        # on the remote machine because our discovery listener can't
                        # parse Distribution's chat-message JSON schema.
                        self.history_service.request_missing_history(peer_addresses=[(host, self.chat_port)])

                    self.user_connected(event.display_name, host)
                elif event.event_type == EventType.LEAVE_CONFIRMED:
                    host, _disc_port = event.user_id.rsplit(":", 1)
                    self.peer_registry.remove_peer(host, self.chat_port)
                    self.user_disconnected(event.display_name, host)
            except Exception as e:
                logger.warning("Membership event handler failed: %s", e)

        # Subscribe BEFORE start() so the subscriber catches every event
        # produced by start() (the local JOIN_ACCEPTED for the seed, or the
        # snapshot replay for a joiner).
        self.discover_service.subscribe_membership_events(handle_membership_event)

        # Run bootstrap synchronously. Bootstrap completes in <1s on success
        # and fails in <bootstrap_timeout (5s) if the seed is unreachable —
        # both fast enough to block the Flask request. Running synchronously
        # ensures the connect-response render sees the populated user list
        # (the UI has no SSE/polling; the response render is the only chance
        # to show the initial roster).
        try:
            self.discover_node.start(display_name=username)
        except Exception as e:
            logger.warning("DiscoveryNode.start() failed: %s", e)

        if self.history_service is not None:
            message_history = self.history_service.get_recent_messages(100)
            for message in message_history:
                self._messages.append(
                    {
                        "role": "assistant",
                        "sender": getattr(message, "sender", getattr(message, "sender_ip", "")),
                        "timestamp": getattr(message, "timestamp", time()),
                        "content": getattr(message, "content", ""),
                    }
                )

        self._refresh("users", self._users)
        self._refresh("messages", self._messages)

    def disconnect(self, username: str) -> None:
        if self.discover_node:
            self.discover_node.stop()
        self.user_disconnected(username, "0.0.0.0")
        self.discover_node = None
        self.discover_service = None

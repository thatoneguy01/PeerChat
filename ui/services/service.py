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
        # Port Distribution's BroadcastNode listens on. main.py overrides
        # peer_registry; if it also changes the BroadcastNode port, it should
        # set chat_service.chat_port to match. Default matches main.py:29.
        self.chat_port = 5678

    def get_users(self) -> list[UserRecord]:
        return self._users

    def get_messages(self) -> list[MessageRecord]:
        return self._messages

    def post_message(self, content: str) -> None:
        # self._messages.append({"timestamp":time(), "content": content})
        self.message_out(content)

    def message_received(self, msg: Message) -> None:
        self._messages.append({"sender": msg.sender, "timestamp": msg.timestamp, "content": msg.content})
        self._refreshes.get("messages", lambda: None)(self._messages)  # trigger a refresh of the messages partial

    def user_connected(self, username: str) -> None:
        if not any(u.get("name") == username for u in self._users):
            self._users.append({"name": username, "status": "Online"})
        self._refreshes.get("users", lambda: None)(self._users)  # trigger a refresh of the users partial

    def user_disconnected(self, username: str) -> None:
        self._users[:] = [u for u in self._users if u.get("name") != username]
        self._refreshes.get("users", lambda: None)(self._users)  # trigger a refresh of the users partial

    def connect(self, username: str, ip: str) -> None:
        if username and not any(u.get("name") == username for u in self._users):
            self._users.append({"name": username, "status": "Online"})

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
            )

        self.discover_node = DiscoveryNode(
            room_id="default", config=config, storage_dir=storage_dir
        )
        self.discover_service = self.discover_node.service

        # Capture the Flask app while we're still inside the request context.
        # The membership subscriber may fire on a non-request thread (the
        # discovery listener / tick / heartbeat threads), so the refresh
        # callbacks (which call render_template) need an app context pushed
        # manually. Without this, every refresh logs
        # "Working outside of application context".
        flask_app = current_app._get_current_object() if has_app_context() else None

        def _refresh(key: str, payload):
            cb = self._refreshes.get(key)
            if cb is None:
                return
            try:
                if flask_app is not None:
                    with flask_app.app_context():
                        cb(payload)
                else:
                    cb(payload)
            except Exception as e:
                logger.warning("Refresh callback for %s failed: %s", key, e)

        def handle_membership_event(event, delta):
            try:
                if event.event_type == EventType.JOIN_ACCEPTED:
                    host, _disc_port = event.user_id.rsplit(":", 1)
                    # Distribution's BroadcastNode listens on self.chat_port,
                    # NOT on the discovery TCP port encoded in event.user_id.
                    # Sending chat to the discovery port produces the
                    # "Incoming frame size 1195725856" (= ASCII "GET ") errors.
                    self.peer_registry.add_peer(host, self.chat_port, event.public_key or b"")
                    if event.display_name and not any(
                        u.get("name") == event.display_name for u in self._users
                    ):
                        self._users.append({"name": event.display_name, "status": "Online"})
                    _refresh("users", self._users)
                elif event.event_type == EventType.LEAVE_CONFIRMED:
                    host, _disc_port = event.user_id.rsplit(":", 1)
                    self.peer_registry.remove_peer(host, self.chat_port)
                    self._users[:] = [u for u in self._users if u.get("name") != event.display_name]
                    _refresh("users", self._users)
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

        _refresh("users", self._users)
        _refresh("messages", self._messages)

from __future__ import annotations

import logging
import tempfile
from typing import TYPE_CHECKING, Callable
from time import sleep, time
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
        # Distribution's BroadcastNode. main.py sets this at startup so
        # DiscoveryNode can route discovery traffic through the shared
        # WebSocket transport instead of standing up its own listener.
        self.broadcast_node = None
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
        # Wired in main.py (e.g. BroadcastNode.decrypt_for_display) for encrypted history replay.
        self.prepare_message: Callable[[Message], None] = lambda _msg: None

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
        # Discovery branch — same pattern History uses below. JOIN_REQUEST,
        # JOIN_RESPONSE, gossip events, and heartbeats arrive here when
        # Distribution's on_message fires for them. If it's one of ours,
        # handle_message returns {"handled": True} and we don't fall through
        # to chat display / history.
        if self.discover_node is not None and self.discover_node.handle_message(msg).get("handled"):
            return
        if self.history_service is not None and self.history_service.handle_message(msg).get("handled"):
            return
        self._messages.append({"sender": msg.sender, "timestamp": msg.timestamp, "content": msg.content})
        self._refresh("messages", self._messages)
        self._refreshes.get("messages", lambda _: None)(self._messages)

    def user_connected(self, username: str, ip: str = "0.0.0.0") -> None:
        if not any(u.get("name") == username for u in self._users):
            self._users.append({"name": username, "status": "Online", "ip": ip})
        self._refresh("users", self._users)
        self._refreshes.get("users", lambda _: None)(self._users)

    def user_disconnected(self, username: str, ip: str = "0.0.0.0") -> None:
        self._users[:] = [u for u in self._users if u.get("name") != username]
        self._refresh("users", self._users)
        self._refreshes.get("users", lambda _: None)(self._users)

    def connect(self, username: str, ip: str) -> None:
        if username and not any(u.get("name") == username for u in self._users):
            self._users.append({"name": username, "status": "Online", "ip": ip if ip else "0.0.0.0"})

        # After consolidation the discovery layer has no port of its own.
        # advertise_address IS Distribution's chat port. Every peer identifies
        # every other peer by lan_ip:chat_port.
        broadcast_node = getattr(self, "broadcast_node", None)
        if broadcast_node is not None:
            advertise_address = broadcast_node.address  # "lan_ip:5678"
        else:
            # Fallback for tests / standalone use without main.py wiring.
            advertise_address = f"{get_lan_ip()}:{self.chat_port}"

        # Use a fresh on-disk storage dir per connect so stale checkpoints
        # from prior test runs don't get recovered and confuse the state
        # machine ("Invalid transition" warnings on re-join).
        storage_dir = tempfile.mkdtemp(prefix="peerchat_")

        if ip == "":
            config = DiscoveryConfig(
                advertise_address=advertise_address,
                bootstrap_timeout=5.0,
                public_key_override=self.public_key_pem,
            )
        else:
            # Accept either "host" or "host:port" for the seed. Default port
            # is the chat port (5678) since everyone advertises on it.
            seed = ip if ":" in ip else f"{ip}:{self.chat_port}"
            config = DiscoveryConfig(
                advertise_address=advertise_address,
                bootstrap_peers=[seed],
                bootstrap_timeout=5.0,
                public_key_override=self.public_key_pem,
            )

        # broadcast_node is set on this service by main.py at app startup.
        # When set, DiscoveryNode routes its wire traffic through Distribution's
        # BroadcastNode (port 5678) instead of its own WebSocket listener.
        # When None (e.g. unit tests, legacy callers), DiscoveryNode falls
        # back to the old transport. Always set in the real app.
        broadcast_node = getattr(self, "broadcast_node", None)

        self.discover_node = DiscoveryNode(
            room_id="default", config=config, storage_dir=storage_dir,
            broadcast_node=broadcast_node,
        )
        self.discover_service = self.discover_node.service

        # Register the lazy pubkey-registration hook on Distribution's
        # BroadcastNode. This runs BEFORE Distribution's verify() check on
        # every incoming message; when the message is a discovery envelope
        # carrying its sender's pubkey, the hook plants it in peer_registry
        # so verify() can find it (trust-on-first-use bootstrap). For
        # non-discovery messages the hook is a no-op.
        if broadcast_node is not None and hasattr(broadcast_node, "pre_verify_hook"):
            broadcast_node.pre_verify_hook = self.discover_node.lazy_register_pubkey
            logger.info("registered_pre_verify_hook on BroadcastNode")
        elif broadcast_node is not None:
            logger.warning(
                "broadcast_node has no pre_verify_hook attribute — "
                "Distribution PR not yet merged. Discovery messages will be "
                "dropped by Distribution's verify() until the hook lands."
            )

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
                        self.history_service.request_missing_history()

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

        if self.history_service is not None and ip:
            sleep(1.0)
            self.history_service.request_missing_history()

        if self.history_service is not None:
            message_history = self.history_service.get_recent_messages(100)
            for message in message_history:
                wire_msg = Message(
                    content=getattr(message, "content", ""),
                    sender=getattr(message, "sender", getattr(message, "sender_ip", "")),
                )
                self.prepare_message(wire_msg)
                self._messages.append(
                    {
                        "role": "assistant",
                        "sender": getattr(message, "sender", getattr(message, "sender_ip", "")),
                        "timestamp": getattr(message, "timestamp", time()),
                        "content": wire_msg.content,
                    }
                )

        self._refresh("users", self._users)
        self._refresh("messages", self._messages)
        self._refreshes.get("users", lambda _: None)(self._users)
        self._refreshes.get("messages", lambda _: None)(self._messages) 

    def disconnect(self, username: str) -> None:
        if self.discover_node:
            self.discover_node.stop()
        self.user_disconnected(username, "0.0.0.0")
        self.discover_node = None
        self.discover_service = None

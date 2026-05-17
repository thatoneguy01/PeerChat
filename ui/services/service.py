from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Callable
from time import time
from .contracts import MessageRecord, UserRecord
from distribution import Message
from distribution.peer_registry import PeerRegistry, InMemoryRegistry
from peer_discovery.membership_integration.service import MembershipService
from peer_discovery.network.config import DiscoveryConfig
from peer_discovery.network.discovery_node import DiscoveryNode
from peer_discovery.network.net_utils import get_lan_ip, pick_free_port
from peer_discovery.membership.models import EventType
from utils import get_external_ip



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

        if ip == "":
            config = DiscoveryConfig(
                advertise_address=advertise_address,
                listen_port=listen_port,
            )
        else:
            # Accept either "host" or "host:port" for the seed. If only a
            # host is given, default to port 8001 (the seed convention).
            seed = ip if ":" in ip else f"{ip}:8001"
            config = DiscoveryConfig(
                advertise_address=advertise_address,
                listen_port=listen_port,
                bootstrap_peers=[seed],
            )

        self.discover_node = DiscoveryNode(
            room_id="default", config=config, storage_dir="../storage"
        )
        self.discover_service = self.discover_node.service

        # Subscribe BEFORE start() so we catch every JOIN_ACCEPTED as it
        # arrives during bootstrap (apply_remote_snapshot dispatches each
        # event through the notifier).
        def handle_membership_event(event, delta):
            if event.event_type == EventType.JOIN_ACCEPTED:
                host, port_str = event.user_id.rsplit(":", 1)
                self.peer_registry.add_peer(host, int(port_str), event.public_key or b"")
                self.user_connected(event.display_name)
            elif event.event_type == EventType.LEAVE_CONFIRMED:
                host, port_str = event.user_id.rsplit(":", 1)
                self.peer_registry.remove_peer(host, int(port_str))
                self.user_disconnected(event.display_name)

        self.discover_service.subscribe_membership_events(handle_membership_event)

        # Run bootstrap on a background thread so the Flask request returns
        # immediately. The subscriber above populates the user list
        # incrementally as JOIN_ACCEPTED events flow in from the snapshot.
        threading.Thread(
            target=self.discover_node.start,
            kwargs={"display_name": username},
            daemon=True,
            name="discovery-start",
        ).start()

        self._refreshes.get("users", lambda: None)(self._users)
        self._refreshes.get("messages", lambda: None)(self._messages)

from __future__ import annotations

from typing import TYPE_CHECKING, Callable
from time import time
from .contracts import MessageRecord, UserRecord
from distribution import Message
from distribution.peer_registry import PeerRegistry
from peer_discovery.membership_integration.service import MembershipService
from peer_discovery.network.config import DiscoveryConfig
from peer_discovery.network.discovery_node import DiscoveryNode
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
        self.peer_registry = None
        self.history_service = None

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
        if self.history_service is not None and self.history_service.handle_message(
            msg
        ).get("handled"):
            return
        self._messages.append({"sender": msg.sender_ip, "timestamp": msg.timestamp, "content": msg.content})
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
        if (ip == ""):
            config = DiscoveryConfig(advertise_address="127.0.0.1:8001", listen_port=8001)
            self.discover_node = DiscoveryNode(room_id="default", config=config, storage_dir="../storage")
            self.discover_service = self.discover_node.service
            self.discover_node.start(display_name=username)
            mebership_snapshot = self.discover_node.service.get_membership_snapshot()
            connected_users = []
        else:
            config = DiscoveryConfig(advertise_address=f"127.0.0.1:8001", listen_port=8001, bootstrap_peers=[f"{ip}:8001"])
            node = DiscoveryNode(room_id="default", config=config, storage_dir="../storage")
            self.discover_node = node
            self.discover_service = self.discover_node.service
            self.discover_node.start(display_name=username)
            mebership_snapshot = self.discover_node.service.get_membership_snapshot()
            for member in mebership_snapshot.members.values():
                self.peer_registry.add_peer(member.user_id.split(":")[0], int(member.user_id.split(":")[1]), member.public_key)
            connected_users = [user.display_name for user in mebership_snapshot.members.values()]
        message_history = (
            self.history_service.get_recent_messages(100)
            if self.history_service is not None
            else []
        )

        def handle_membership_event(event, delta):
            if event.event_type == EventType.JOIN_ACCEPTED:
                self.peer_registry.add_peer(event.user_id.split(":")[0], int(event.user_id.split(":")[1]), event.public_key)
                self.user_connected(event.display_name)
            elif event.event_type == EventType.LEAVE_CONFIRMED:
                self.peer_registry.remove_peer(event.user_id.split(":")[0], int(event.user_id.split(":")[1]), event.public_key)
                self.user_disconnected(event.display_name)

        self.discover_service.subscribe_membership_events(handle_membership_event)

        for user in connected_users:
            self._users.append({"name": user.username, "status": "Online"})
        for message in message_history:
            self._messages.append({"sender": message.sender_ip, "timestamp": message.timestamp, "content": message.content})
        self._refreshes.get("users", lambda: None)(self._users)  # trigger a refresh of the users partial
        self._refreshes.get("messages", lambda: None)(self._messages)  # trigger a refresh of the messages partial

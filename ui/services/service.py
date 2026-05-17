from __future__ import annotations

import logging

from .contracts import MessageRecord, UserRecord
from distribution import Message
from peer_discovery.network.config import DiscoveryConfig
from peer_discovery.network.discovery_node import DiscoveryNode
from peer_discovery.membership.models import EventType
from security.key_storage import InMemoryKeyStore, MissingKeyError
from security.payload_encryption import (
    PayloadEncryptionError,
    decrypt_payload,
    encrypt_payload,
)

logger = logging.getLogger(__name__)


class Service:
    _BASE_MESSAGES: list[MessageRecord] = []

    def __init__(self, refreshes: dict[str, callable]) -> None:
        self._messages: list[MessageRecord] = []
        self._users: list[UserRecord] = []
        self._refreshes = refreshes
        self.message_out = lambda msg: None
        self.message_in = lambda content, timestamp, sender_ip: None
        self.discover_node = None
        self.discover_service = None
        self.peer_registry = None
        self.history_service = None
        self.key_store: InMemoryKeyStore | None = None
        self.node_address: str = ""

    def get_users(self) -> list[UserRecord]:
        return self._users

    def get_messages(self) -> list[MessageRecord]:
        return self._messages

    def post_message(self, content: str) -> None:
        msg = Message(content=content, sender=self.node_address or "local")
        try:
            msg = encrypt_payload(
                msg,
                self._recipient_pubkeys(),
                own_user_id=self.node_address,
            )
        except Exception:
            logger.warning("payload encryption failed; sending plaintext", exc_info=True)
        self.message_out(msg)

    def use_history(self, history_service) -> None:
        self.history_service = history_service

    def message_received(self, msg: Message) -> None:
        if self.history_service and self.history_service.handle_message(msg).get("handled"):
            return
        display_content = self._decrypt_for_display(msg)
        self._messages.append(
            {"sender": msg.sender, "timestamp": msg.timestamp, "content": display_content}
        )
        self._refreshes.get("messages", lambda: None)(self._messages)  # trigger a refresh of the messages partial

    def _recipient_pubkeys(self) -> dict[str, bytes]:
        pubkeys: dict[str, bytes] = {}
        if self.node_address and self.key_store is not None:
            try:
                pubkeys[self.node_address] = self.key_store.get_public_key_pem()
            except MissingKeyError:
                pass
        if self.peer_registry is not None and hasattr(self.peer_registry, "get_pub_key"):
            for host, port in self.peer_registry.get_peers():
                user_id = f"{host}:{port}"
                pub = self.peer_registry.get_pub_key(host, port)
                if pub:
                    pubkeys[user_id] = pub.encode("utf-8") if isinstance(pub, str) else pub
        return pubkeys

    def _decrypt_for_display(self, msg: Message) -> str:
        if self.key_store is None or not self.node_address:
            return msg.content
        try:
            private_pem = self.key_store.get_private_key()
            return decrypt_payload(msg, self.node_address, private_pem).content
        except PayloadEncryptionError:
            logger.warning("could not decrypt message %s", msg.id)
            return "[encrypted]"
        except MissingKeyError:
            return msg.content

    def user_connected(self, username: str, ip: str = "0.0.0.0") -> None:
        if not any(u.get("name") == username for u in self._users):
            self._users.append({"name": username, "status": "Online", "ip": ip})
        self._refreshes.get("users", lambda: None)(self._users)  # trigger a refresh of the users partial

    def user_disconnected(self, username: str, ip: str = "0.0.0.0") -> None:
        self._users[:] = [u for u in self._users if u.get("name") != username]
        self._refreshes.get("users", lambda: None)(self._users)  # trigger a refresh of the users partial

    def connect(self, username: str, ip: str) -> None:
        if username and not any(u.get("name") == username for u in self._users):
            self._users.append({"name": username, "status": "Online", "ip": ip if ip else "0.0.0.0"})
        if (ip == ""):
            config = DiscoveryConfig(advertise_address="127.0.0.1:8001", listen_port=8001)
            self.discover_node = DiscoveryNode(room_id="default", config=config, storage_dir="../storage")
            self.discover_service = self.discover_node.service
            self.discover_node.start(display_name=username)
            mebership_snapshot = self.discover_node.service.get_membership_snapshot()
            connected_users = []
            message_history = []
        else:
            config = DiscoveryConfig(advertise_address=f"127.0.0.1:8001", listen_port=8001, bootstrap_peers=[f"{ip}:8001"])
            node = DiscoveryNode(room_id="default", config=config, storage_dir="../storage")
            self.discover_node = node
            self.discover_service = self.discover_node.service
            self.discover_node.start(display_name=username)
            mebership_snapshot = self.discover_node.service.get_membership_snapshot()
            for member in mebership_snapshot.members.values():
                self.peer_registry.add_peer(member.user_id.split(":")[0], int(member.user_id.split(":")[1]), member.public_key)
                self.history_service.request_missing_history(peer_addresses=[(member.user_id.split(":")[0], int(member.user_id.split(":")[1]))])
            connected_users = [user.display_name for user in mebership_snapshot.members.values()]
            message_history = self.history_service.get_recent_messages(100)

        def handle_membership_event(event, delta):
            if event.event_type == EventType.JOIN_ACCEPTED:
                self.peer_registry.add_peer(event.user_id.split(":")[0], int(event.user_id.split(":")[1]), event.public_key)
                self.history_service.request_missing_history(peer_addresses=[(event.user_id.split(":")[0], int(event.user_id.split(":")[1]))])
                self.user_connected(event.display_name, event.user_id.split(":")[0])
            elif event.event_type == EventType.LEAVE_CONFIRMED:
                self.peer_registry.remove_peer(event.user_id.split(":")[0], int(event.user_id.split(":")[1]), event.public_key)
                self.user_disconnected(event.display_name, event.user_id.split(":")[0])

        self.discover_service.subscribe_membership_events(handle_membership_event)

        for user in connected_users:
            self._users.append({"name": user.username, "status": "Online", "ip": user.ip})
        for message in message_history:
            sender = getattr(message, "sender_ip", None) or getattr(message, "sender", "")
            wire_msg = Message(content=message.content, sender=sender)
            self._messages.append(
                {
                    "role": "assistant",
                    "sender": sender,
                    "timestamp": message.timestamp,
                    "content": self._decrypt_for_display(wire_msg),
                }
            )
        self._refreshes.get("users", lambda: None)(self._users)  # trigger a refresh of the users partial
        self._refreshes.get("messages", lambda: None)(self._messages)  # trigger a refresh of the messages partial

    def disconnect(self, username: str) -> None:
        if self.discover_node:
            self.discover_node.stop()
        self.user_disconnected(username, "0.0.0.0")
        self.discover_node = None
        self.discover_service = None
from __future__ import annotations

from time import time
from .contracts import MessageRecord, UserRecord
from distribution import Message


class Service:
    _BASE_MESSAGES: list[MessageRecord] = []

    def __init__(self, refreshes: dict[str, callable]) -> None:
        self._messages: list[MessageRecord] = self._BASE_MESSAGES
        self._users: list[UserRecord] = []
        self._refreshes = refreshes
        self.message_out = lambda content: None
        self.message_in = lambda content, timestamp, sender_ip: None

    def get_users(self) -> list[UserRecord]:
        return self._users

    def get_messages(self) -> list[MessageRecord]:
        return self._messages

    def post_message(self, content: str) -> None:
        # self._messages.append({"timestamp":time(), "content": content})
        self.message_out(content)

    def message_received(self, msg: Message) -> None:
        self._messages.append({"sender": msg.sender_ip, "timestamp": msg.timestamp, "content": msg.content})
        self._refreshes.get("messages", lambda: None)(self._messages)  # trigger a refresh of the messages partial

    def user_connected(self, username: str, ip: str) -> None:
        #call in here from your discovery mechanism when you detect a new user
        if not any(u.get("name") == username for u in self._users):
            self._users.append({"name": username, "status": "Online"})
        self._refreshes.get("users", lambda: None)(self._users)  # trigger a refresh of the users partial

    def user_disconnected(self, username: str) -> None:
        #call in here from your discovery mechanism when you detect a user has disconnected
        self._users[:] = [u for u in self._users if u.get("name") != username]
        self._refreshes.get("users", lambda: None)(self._users)  # trigger a refresh of the users partial

    def connect(self, username: str) -> None:
        if username and not any(u.get("name") == username for u in self._users):
            self._users.append({"name": username, "status": "Online"})
        # Call out here to discovery
        connected_users = [] #call here to get a list of currently connected users from your discovery mechanism
        message_history = [] # call here to get recent message history from your message distribution mechanism
        for user in connected_users:
            self._users.append({"name": user.username, "status": "Online"})
        for message in message_history:
            self._messages.append({"sender": message.sender_ip, "timestamp": message.timestamp, "content": message.content})
        self._refreshes.get("users", lambda: None)(self._users)  # trigger a refresh of the users partial
        self._refreshes.get("messages", lambda: None)(self._messages)  # trigger a refresh of the messages partial

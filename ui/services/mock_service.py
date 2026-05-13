from __future__ import annotations

from copy import deepcopy

from .contracts import MessageRecord, UserRecord


class MockService:
    _BASE_MESSAGES: list[MessageRecord] = [
        {"role": "assistant", "content": "Welcome. This scaffold is ready for a real chat backend."},
    ]

    def __init__(self) -> None:
        self._users: list[UserRecord] = [
            {"name": "Ava Patel", "status": "Online"},
            {"name": "Noah Kim", "status": "In chat"},
            {"name": "Mia Chen", "status": "Away"},
            {"name": "Liam Garcia", "status": "Offline"},
        ]
        self._messages_by_user: dict[str, list[MessageRecord]] = {}

    def get_users(self) -> list[UserRecord]:
        return self._users

    def _message_key(self, selected_user: str) -> str:
        return selected_user.strip() or "__default__"

    def _messages_for(self, selected_user: str) -> list[MessageRecord]:
        key = self._message_key(selected_user)
        if key not in self._messages_by_user:
            self._messages_by_user[key] = deepcopy(self._BASE_MESSAGES)
        return self._messages_by_user[key]

    def get_messages(self, selected_user: str) -> list[MessageRecord]:
        return self._messages_for(selected_user)

    def post_user_message(self, selected_user: str, content: str) -> None:
        messages = self._messages_for(selected_user)
        messages.append({"role": "user", "content": content})
        messages.append(
            {
                "role": "assistant",
                "content": "Hook this route to your model, queue, or websocket bridge.",
            }
        )

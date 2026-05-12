from __future__ import annotations

from .contracts import MessageRecord, UserRecord


class MockService:
    def __init__(self) -> None:
        self._users: list[UserRecord] = [
            {"name": "Ava Patel", "status": "Online"},
            {"name": "Noah Kim", "status": "In chat"},
            {"name": "Mia Chen", "status": "Away"},
            {"name": "Liam Garcia", "status": "Offline"},
        ]
        self._messages: list[MessageRecord] = [
            {"role": "assistant", "content": "Welcome. This scaffold is ready for a real chat backend."},
        ]

    def get_users(self) -> list[UserRecord]:
        return self._users

    def get_messages(self) -> list[MessageRecord]:
        return self._messages

    def post_user_message(self, content: str) -> None:
        self._messages.append({"role": "user", "content": content})
        self._messages.append(
            {
                "role": "assistant",
                "content": "Hook this route to your model, queue, or websocket bridge.",
            }
        )

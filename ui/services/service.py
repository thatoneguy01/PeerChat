from __future__ import annotations

from .contracts import MessageRecord, UserRecord


class Service:
    def __init__(self) -> None:
        self._messages: list[MessageRecord] = []

    def get_users(self) -> list[UserRecord]:
        # TODO: Load users from real data source instead of mock seed.
        return []

    def get_messages(self) -> list[MessageRecord]:
        # TODO: Load message history from real data source instead of in-memory list.
        return self._messages

    def post_user_message(self, content: str) -> None:
        self._messages.append({"role": "user", "content": content})
        # TODO: Implement real message generation + persistence integration here.

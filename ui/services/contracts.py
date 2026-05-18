from __future__ import annotations

from typing import Protocol

UserRecord = dict[str, str, str]
MessageRecord = dict[int, str]


class ChatService(Protocol):
    def get_users(self) -> list[UserRecord]:
        ...

    def get_messages(self) -> list[MessageRecord]:
        ...

    def post_message(self, content: str) -> None:
        ...

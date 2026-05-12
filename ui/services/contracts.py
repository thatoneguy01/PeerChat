from __future__ import annotations

from typing import Protocol

UserRecord = dict[str, str]
MessageRecord = dict[str, str]


class ChatService(Protocol):
    def get_users(self) -> list[UserRecord]:
        ...

    def get_messages(self) -> list[MessageRecord]:
        ...

    def post_user_message(self, content: str) -> None:
        ...

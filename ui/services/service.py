from __future__ import annotations

from copy import deepcopy

from .contracts import MessageRecord, UserRecord


class Service:
    _BASE_MESSAGES: list[MessageRecord] = []

    def __init__(self) -> None:
        self._messages_by_user: dict[str, list[MessageRecord]] = {}

    def get_users(self) -> list[UserRecord]:
        # TODO: Load users from real data source instead of mock seed.
        return []

    def _message_key(self, selected_user: str) -> str:
        return selected_user.strip() or "__default__"

    def _messages_for(self, selected_user: str) -> list[MessageRecord]:
        key = self._message_key(selected_user)
        if key not in self._messages_by_user:
            self._messages_by_user[key] = deepcopy(self._BASE_MESSAGES)
        return self._messages_by_user[key]

    def get_messages(self, selected_user: str) -> list[MessageRecord]:
        # TODO: Load message history from real data source instead of in-memory list.
        return self._messages_for(selected_user)

    def post_user_message(self, selected_user: str, content: str) -> None:
        self._messages_for(selected_user).append({"role": "user", "content": content})
        # TODO: Implement real message generation + persistence integration here.

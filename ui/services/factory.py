from __future__ import annotations

from .contracts import ChatService
from .mock_service import MockService
from .service import Service


def create_chat_service(mock_data_enabled: bool, refreshes: dict[str, callable]) -> ChatService:
    if mock_data_enabled:
        return MockService()
    return Service(refreshes=refreshes)

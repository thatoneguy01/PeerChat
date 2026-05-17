"""UI service: plaintext send; receive expects plaintext from Distribution."""

import pytest

from distribution import Message
from ui.services.service import Service


def _make_service() -> Service:
    return Service(refreshes={"messages": lambda _: None, "users": lambda _: None})


def test_post_message_passes_plaintext_string_to_distribution():
    sent: list[str] = []
    service = _make_service()
    service.message_out = sent.append

    service.post_message("team update")

    assert sent == ["team update"]


def test_message_received_displays_plaintext_content():
    receiver = _make_service()
    receiver.history_service = None
    receiver.message_received(
        Message(content="hello alice", sender="127.0.0.1:5002")
    )

    assert receiver.get_messages()[-1]["content"] == "hello alice"

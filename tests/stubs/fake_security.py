"""
Stub for the Security team's module.

Implements the contract in docs/contract_security.md with a trivial signature
scheme: signature = "fake-sig-" + msg.id. Real Security replaces this.
"""

from distribution import Message


def sign(msg: Message) -> Message:
    msg.signature = f"fake-sig-{msg.id}"
    return msg


def verify(msg: Message) -> bool:
    return msg.signature == f"fake-sig-{msg.id}"

"""
Storage package for the Message History module.

This package exposes the main classes used by other teams:
- Message
- LocalMessageStore
"""

from .models import Message
from .local_message_store import LocalMessageStore

__all__ = ["Message", "LocalMessageStore"]
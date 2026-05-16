"""
End-to-end secure chat pipeline: encrypt -> sign (send), verify -> decrypt (receive).

Uses hybrid RSA+AES per recipient and RSA-PSS signatures on Message.content.
"""

from __future__ import annotations

import logging

from distribution.message import Message
from security.encryption import (
    decrypt_broadcast_content,
    encrypt_broadcast_content,
    get_public_key_pem,
)
from security.key_storage import InMemoryKeyStore, MissingKeyError
from security.message_integrity import sign_message, verify_message
from security.roster import PubkeyRoster
from security.rsa_keys import generate_rsa_keypair

logger = logging.getLogger(__name__)


class SecureChatSession:
    """
    Per-node security context: private key, pubkey roster, encrypt/sign helpers.

    user_id should match Discovery's member id when available; until then use
    node address (host:port) as user_id in ciphertext boxes.
    """

    def __init__(
        self,
        *,
        user_id: str,
        key_store: InMemoryKeyStore | None = None,
        roster: PubkeyRoster | None = None,
    ) -> None:
        if not user_id:
            raise ValueError("user_id is required")
        self.user_id = user_id
        self._key_store = key_store or InMemoryKeyStore()
        self._roster = roster or PubkeyRoster()
        self._ensure_keypair()

    @property
    def roster(self) -> PubkeyRoster:
        return self._roster

    @property
    def public_key_pem(self) -> bytes:
        return get_public_key_pem(self._key_store.get_private_key())

    def load_existing_key(self, private_key_pem: bytes) -> None:
        """Load RSA private key PEM from persistent storage (e.g. keyring)."""
        self._key_store.set_private_key(private_key_pem)
        self._roster.register_peer(self.user_id, self.public_key_pem)

    def register_peer(self, user_id: str, public_key_pem: bytes) -> None:
        self._roster.register_peer(user_id, public_key_pem)

    def _ensure_keypair(self) -> None:
        if self._key_store.has_private_key():
            if self.user_id not in self._roster:
                self._roster.register_peer(self.user_id, self.public_key_pem)
            return
        private_pem, public_pem = generate_rsa_keypair()
        self._key_store.set_private_key(private_pem)
        self._roster.register_peer(self.user_id, public_pem)

    def prepare_outgoing(self, *, plaintext: str, sender_address: str) -> Message:
        """
        Encrypt for all roster members, then sign. Caller passes result to broadcast().
        """
        recipients = self._roster.all_pubkeys()
        if not recipients:
            raise ValueError("roster is empty; register peer pubkeys before sending")

        if self.user_id not in recipients:
            recipients = dict(recipients)
            recipients[self.user_id] = self.public_key_pem

        wire_content = encrypt_broadcast_content(
            plaintext=plaintext,
            recipient_pubkeys=recipients,
        )
        msg = Message(content=wire_content, sender=sender_address)
        return sign_message(msg, self._key_store.get_private_key())

    def open_incoming(self, msg: Message) -> str | None:
        """
        Verify signature using sender's pubkey from roster, then decrypt our box.
        Returns plaintext or None if verify/decrypt fails.
        """
        sender_key = self._sender_user_id(msg)
        pubkey = self._roster.get_public_key(sender_key)
        if pubkey is None:
            logger.warning("no pubkey for sender %s", sender_key)
            return None
        if not verify_message(msg, pubkey):
            logger.warning("signature verification failed for %s", msg.id)
            return None
        try:
            return decrypt_broadcast_content(
                content=msg.content,
                own_user_id=self.user_id,
                private_key_pem=self._key_store.get_private_key(),
            )
        except Exception:
            logger.warning("decrypt failed for message %s", msg.id, exc_info=True)
            return None

    def _sender_user_id(self, msg: Message) -> str:
        """Map Message.sender (host:port) to roster user_id until Discovery adds a field."""
        if msg.sender in self._roster:
            return msg.sender
        return msg.sender

    def try_get_private_key(self) -> bytes | None:
        try:
            return self._key_store.get_private_key()
        except MissingKeyError:
            return None

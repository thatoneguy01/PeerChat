"""Constants for PeerChat message encryption (AES-256-GCM v1)."""

MAGIC = 0x4D
ENVELOPE_VERSION = 0x01
CIPHER_SUITE_AES_256_GCM = 0x01

FLAG_KEY_ID_PRESENT = 0x01

AES_KEY_SIZE = 32
NONCE_SIZE = 12
GCM_TAG_SIZE = 16
SALT_SIZE = 16
KEY_ID_SIZE = 4

HEADER_SIZE = 20  # magic .. nonce (inclusive)

MAX_PLAINTEXT_BYTES = 262_144
MAX_ENVELOPE_BYTES = 1_048_576

AAD_PREFIX = b"peerchat\x00"
HKDF_INFO_MSG = b"peerchat-msg-v1"
HKDF_INFO_ROTATE = b"peerchat-rotate"

# PeerChat v1: single global room
DEFAULT_ROOM_ID = 0

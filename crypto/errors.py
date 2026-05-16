"""Exception taxonomy for message encryption."""


class CryptoError(Exception):
    """Base exception for encryption layer errors."""


class UnknownSuiteError(CryptoError):
    """Unsupported cipher_suite byte in envelope."""

    def __init__(self, cipher_suite: int) -> None:
        self.cipher_suite = cipher_suite
        super().__init__(f"unsupported cipher suite: {cipher_suite:#04x}")


class UnsupportedEnvelopeVersionError(CryptoError):
    """Unknown envelope_version byte."""

    def __init__(self, version: int) -> None:
        self.version = version
        super().__init__(f"unsupported envelope version: {version:#04x}")


class DecryptFailedError(CryptoError):
    """Decryption failed (wrong key, tampered ciphertext, or bad AAD)."""


class InvalidEnvelopeError(CryptoError):
    """Malformed envelope (truncated, bad magic, oversize)."""


class PlaintextTooLargeError(CryptoError):
    """Plaintext exceeds MAX_PLAINTEXT_BYTES."""

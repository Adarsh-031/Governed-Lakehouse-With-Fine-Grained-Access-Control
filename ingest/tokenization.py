"""Stable pseudonyms, NOT encryption or a guarantee of anonymity."""

import hashlib
import hmac


def patient_token(patient_id: str, key: bytes, key_version: str = "v1") -> str:
    """Use the same canonical ID, key, and version across all related tables.

    Key rotation changes tokens and requires coordinated migration.
    Never use patient names as identity keys. Keep the key outside Git and logs.
    """
    if not patient_id or not patient_id.strip():
        raise ValueError("patient_id must be non-empty")
    if len(key) < 32:
        raise ValueError("Use a randomly generated key of at least 32 bytes")
    message = ("patient:" + patient_id.strip()).encode("utf-8")
    digest = hmac.new(key, message, hashlib.sha256).hexdigest()
    return f"{key_version}:{digest}"

"""Unit tests for HMAC-based patient tokenization."""

import re

import pytest

from ingest.tokenization import patient_token


KEY = b"a" * 32
OTHER_KEY = b"b" * 32
PATIENT_ID = "patient-123"


def test_same_input_and_key_produce_same_token() -> None:
    """Tokenization is stable for the same ID, key, and version."""
    assert patient_token(PATIENT_ID, KEY) == patient_token(PATIENT_ID, KEY)


def test_different_key_produces_different_token() -> None:
    """Rotating the secret key changes the token."""
    assert patient_token(PATIENT_ID, KEY) != patient_token(PATIENT_ID, OTHER_KEY)


def test_different_version_produces_different_token() -> None:
    """The version prefix distinguishes tokenization-key versions."""
    assert patient_token(PATIENT_ID, KEY, key_version="v1") != patient_token(
        PATIENT_ID, KEY, key_version="v2"
    )


def test_patient_id_whitespace_is_stripped() -> None:
    """Leading and trailing whitespace do not change the canonical token."""
    assert patient_token("  patient-123 \t", KEY) == patient_token(PATIENT_ID, KEY)


@pytest.mark.parametrize("patient_id", ["", " ", "\t\n"])
def test_empty_or_whitespace_patient_id_raises_value_error(patient_id: str) -> None:
    """Missing patient IDs are rejected."""
    with pytest.raises(ValueError, match="patient_id must be non-empty"):
        patient_token(patient_id, KEY)


@pytest.mark.parametrize("key", [b"", b"short", b"a" * 31])
def test_key_shorter_than_32_bytes_raises_value_error(key: bytes) -> None:
    """Keys shorter than the required 32 bytes are rejected."""
    with pytest.raises(ValueError, match="at least 32 bytes"):
        patient_token(PATIENT_ID, key)


def test_token_has_version_and_sha256_hex_format() -> None:
    """Tokens use a version prefix followed by a 64-character SHA-256 HMAC."""
    token = patient_token(PATIENT_ID, KEY)
    assert re.fullmatch(r"v1:[0-9a-f]{64}", token)

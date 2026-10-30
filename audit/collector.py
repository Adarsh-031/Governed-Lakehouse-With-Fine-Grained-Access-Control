"""Authenticated collector for Trino HTTP event-listener deliveries (issue #20).

Every accepted event is written to its own file in the restricted spool and
fsynced (file, then directory) before the 200 response is returned. A delivery
that cannot be persisted gets a 5xx so Trino's listener retries it. The batch
writer (#21) reads the spool, normalizes and deduplicates on
(query_id, event_type), and loads the Iceberg audit table.

The collector never logs request bodies: Trino events carry raw SQL, which may
contain identifying literals.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

LOGGER = logging.getLogger("audit.collector")

DEFAULT_MAX_BODY_BYTES = 1024 * 1024
MIN_TOKEN_BYTES = 32
SPOOL_SUFFIX = ".json"
_TEMP_PREFIX = ".incoming-"


class CollectorConfigError(ValueError):
    """Raised when the collector would start with an unsafe configuration."""


@dataclass(frozen=True)
class CollectorSettings:
    token: str
    spool_dir: Path
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES

    def __post_init__(self) -> None:
        if len(self.token.encode()) < MIN_TOKEN_BYTES:
            raise CollectorConfigError(
                f"AUDIT_INGEST_TOKEN must be at least {MIN_TOKEN_BYTES} bytes"
            )
        if self.max_body_bytes < 1:
            raise CollectorConfigError("AUDIT_MAX_BODY_BYTES must be positive")


def settings_from_environment() -> CollectorSettings:
    """Read collector settings; refuse to start without a strong shared token."""
    token = os.environ.get("AUDIT_INGEST_TOKEN", "")
    if not token:
        raise CollectorConfigError("AUDIT_INGEST_TOKEN is required")
    spool = Path(os.environ.get("AUDIT_SPOOL_DIR", "data/audit-spool"))
    try:
        limit = int(os.environ.get("AUDIT_MAX_BODY_BYTES", str(DEFAULT_MAX_BODY_BYTES)))
    except ValueError as exc:
        raise CollectorConfigError("AUDIT_MAX_BODY_BYTES must be an integer") from exc
    return CollectorSettings(token=token, spool_dir=spool, max_body_bytes=limit)


def _fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def prepare_spool(spool_dir: Path) -> None:
    """Create the spool (mode 0700), drop stale partial writes, and check it is writable."""
    spool_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    for stale in spool_dir.glob(f"{_TEMP_PREFIX}*"):
        # Never acknowledged: a crash happened before the atomic rename.
        stale.unlink(missing_ok=True)
    if not os.access(spool_dir, os.W_OK | os.X_OK):
        raise CollectorConfigError(f"audit spool is not writable: {spool_dir}")


def persist_event(spool_dir: Path, event: dict, received_timestamp: str) -> str:
    """Durably write one event; return its spool record id.

    Write to a temporary file, fsync it, atomically rename it into place, then
    fsync the directory so the rename itself survives a crash.
    """
    record_id = f"{time.time_ns():020d}-{uuid.uuid4().hex}"
    payload = json.dumps(
        {"record_id": record_id, "received_timestamp": received_timestamp, "event": event},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    temp_path = spool_dir / f"{_TEMP_PREFIX}{record_id}"
    final_path = spool_dir / f"{record_id}{SPOOL_SUFFIX}"
    fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        try:
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temp_path, final_path)
        _fsync_directory(spool_dir)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return record_id


def _bearer_token_matches(header: str | None, expected: str) -> bool:
    if not header:
        return False
    scheme, _, supplied = header.partition(" ")
    if scheme.lower() != "bearer" or not supplied:
        return False
    return hmac.compare_digest(supplied.strip().encode(), expected.encode())


def _error(status: int, detail: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status, headers=headers)


def create_app(settings: CollectorSettings) -> FastAPI:
    prepare_spool(settings.spool_dir)
    app = FastAPI(
        title="Governed lakehouse audit collector",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/healthz")
    def healthz() -> JSONResponse:
        if not os.access(settings.spool_dir, os.W_OK | os.X_OK):
            return _error(503, "spool unavailable")
        return JSONResponse({"status": "ok"})

    @app.post("/v1/events")
    async def receive_event(request: Request) -> JSONResponse:
        if not _bearer_token_matches(request.headers.get("authorization"), settings.token):
            return _error(401, "unauthorized", {"WWW-Authenticate": "Bearer"})

        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > settings.max_body_bytes:
            return _error(413, "event too large")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > settings.max_body_bytes:
                return _error(413, "event too large")

        try:
            event = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _error(400, "body must be JSON")
        if not isinstance(event, dict):
            return _error(400, "event must be a JSON object")

        received = datetime.now(UTC).isoformat(timespec="microseconds")
        try:
            record_id = persist_event(settings.spool_dir, event, received)
        except OSError:
            # Body deliberately omitted; Trino retries on a 5xx.
            LOGGER.exception("failed to persist audit event")
            return _error(503, "event not persisted")
        return JSONResponse({"status": "stored", "record_id": record_id})

    return app


def create_app_from_environment() -> FastAPI:
    """Uvicorn factory: ``uvicorn --factory audit.collector:create_app_from_environment``."""
    return create_app(settings_from_environment())

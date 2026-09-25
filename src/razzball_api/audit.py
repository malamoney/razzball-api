"""Records each API request in the ``api_request_log`` table.

The raw API key is never stored or logged. The ``api_key`` column receives a
short SHA-256 fingerprint, which is enough to correlate requests made with the
same (possibly unknown) key without turning the log table into a key store.
"""

import hashlib
import logging
from dataclasses import dataclass

from flask import Flask, Response, g, request
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from razzball_api.auth import API_KEY_HEADER
from razzball_api.extensions import db

logger = logging.getLogger(__name__)

UNKNOWN_USER_ID = 0
_MAX_URI_LENGTH = 255
_MAX_DESCRIPTION_LENGTH = 255

_INSERT_SQL = text(
    "INSERT INTO api_request_log "
    "(api_user_id, api_key, uri, method, remote_addr, status_code, description) "
    "VALUES (:api_user_id, :api_key, :uri, :method, :remote_addr, :status_code, "
    ":description)"
)


@dataclass(frozen=True, kw_only=True)
class RequestRecord:
    api_user_id: int
    api_key_fingerprint: str
    uri: str
    method: str
    remote_addr: str
    status_code: int
    description: str


def fingerprint_api_key(api_key: str | None) -> str:
    if not api_key:
        return ""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]


def record_request(engine: Engine, record: RequestRecord) -> None:
    with engine.begin() as conn:
        conn.execute(
            _INSERT_SQL,
            {
                "api_user_id": record.api_user_id,
                "api_key": record.api_key_fingerprint,
                "uri": record.uri[:_MAX_URI_LENGTH],
                "method": record.method,
                "remote_addr": record.remote_addr,
                "status_code": record.status_code,
                "description": record.description[:_MAX_DESCRIPTION_LENGTH],
            },
        )


def register_request_audit(app: Flask, *, exempt_endpoints: frozenset[str]) -> None:
    @app.after_request
    def audit_request(response: Response) -> Response:
        if request.endpoint in exempt_endpoints:
            return response
        record = RequestRecord(
            api_user_id=g.get("api_user_id", UNKNOWN_USER_ID),
            api_key_fingerprint=fingerprint_api_key(
                request.headers.get(API_KEY_HEADER)
            ),
            uri=request.path,
            method=request.method,
            remote_addr=request.remote_addr or "",
            status_code=response.status_code,
            description=response.status,
        )
        try:
            record_request(db.engine, record)
        except SQLAlchemyError:
            # Losing an audit row must not turn a served response into an error.
            logger.exception("Failed to write api_request_log row")
        return response

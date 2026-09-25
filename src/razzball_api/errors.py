"""API error type and application-wide JSON error handlers.

Every error response has the shape
``{"error": true, "status_code": int, "message": str, "description": str}``
with an optional ``"details"`` object, matching the original API's error body.
"""

import logging
from collections.abc import Mapping

from flask import Flask, Response, g, has_app_context, jsonify
from werkzeug.exceptions import HTTPException

from razzball_api.database import DatabaseUnavailableError

logger = logging.getLogger(__name__)

RETRY_AFTER_SECONDS = 30
# Key in flask.g: each request gets a fresh app context, so the record never
# outlives the request that found the outage.
_UNAVAILABLE_KEY = "unavailable_databases"


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        message: str,
        description: str = "",
        *,
        details: Mapping[str, object] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.description = description
        self.details = details
        self.headers = headers


def mark_unavailable(database: str) -> None:
    """Record that ``database`` could not be reached during this request."""
    recorded: set[str] = g.setdefault(_UNAVAILABLE_KEY, set())
    recorded.add(database)


def unavailable_databases() -> frozenset[str]:
    """Databases found unreachable earlier in the current request."""
    if not has_app_context():
        return frozenset()
    return frozenset(g.get(_UNAVAILABLE_KEY, ()))


def error_response(
    status_code: int,
    message: str,
    description: str = "",
    *,
    details: Mapping[str, object] | None = None,
) -> Response:
    body: dict[str, object] = {
        "error": True,
        "status_code": status_code,
        "message": message,
        "description": description,
    }
    if details is not None:
        body["details"] = dict(details)
    response = jsonify(body)
    response.status_code = status_code
    return response


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(ApiError)
    def handle_api_error(error: ApiError) -> Response:
        response = error_response(
            error.status_code, error.message, error.description, details=error.details
        )
        if error.headers:
            response.headers.update(error.headers)
        return response

    @app.errorhandler(DatabaseUnavailableError)
    def handle_database_unavailable(error: DatabaseUnavailableError) -> Response:
        mark_unavailable(error.database)
        # One line, no traceback: the driver's message can include the host.
        if error.error_code is None:
            logger.error("%s database unavailable", error.database)
        else:
            logger.error(
                "%s database unavailable (driver error code %d)",
                error.database,
                error.error_code,
            )
        response = error_response(
            503, "Service Unavailable", "The service is temporarily unavailable."
        )
        if error.retryable:
            response.headers["Retry-After"] = str(RETRY_AFTER_SECONDS)
        return response

    @app.errorhandler(HTTPException)
    def handle_http_exception(error: HTTPException) -> Response:
        # Keep the status code and headers werkzeug chose (e.g. Allow on 405).
        original = error.get_response()
        response = error_response(
            error.code or 500, error.name, error.description or ""
        )
        for key, value in original.headers.items():
            if key.lower() not in {"content-type", "content-length"}:
                response.headers[key] = value
        return response

    @app.errorhandler(Exception)
    def handle_unexpected(error: Exception) -> Response:
        logger.exception("Unhandled exception while processing request")
        return error_response(
            500, "Internal Server Error", "The server encountered an internal error."
        )

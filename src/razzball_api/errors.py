"""API error type and application-wide JSON error handlers.

Every error response has the shape
``{"error": true, "status_code": int, "message": str, "description": str}``
with an optional ``"details"`` object, matching the original API's error body.
"""

import logging
from collections.abc import Mapping

from flask import Flask, Response, jsonify
from werkzeug.exceptions import HTTPException

logger = logging.getLogger(__name__)


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

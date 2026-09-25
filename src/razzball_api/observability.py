"""Logging setup, request correlation IDs, and access log lines."""

import logging
import re
import sys
import time
import uuid

from flask import Flask, Response, g, has_request_context, request

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_HANDLER_NAME = "razzball_api"

access_logger = logging.getLogger("razzball_api.access")


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = g.get("request_id", "-") if has_request_context() else "-"
        return True


def configure_logging(level: str) -> None:
    """Send the package's logs to stderr. Safe to call more than once."""
    package_logger = logging.getLogger("razzball_api")
    package_logger.setLevel(level)
    if any(h.get_name() == _HANDLER_NAME for h in package_logger.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.set_name(_HANDLER_NAME)
    handler.addFilter(_RequestIdFilter())
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    package_logger.addHandler(handler)


def register_request_logging(app: Flask) -> None:
    @app.before_request
    def assign_request_id() -> None:
        # Reuse a well-formed upstream ID so logs line up across the proxy;
        # anything else is replaced rather than echoed into our logs.
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        g.request_id = (
            incoming if _VALID_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        )
        g.request_started = time.perf_counter()

    @app.after_request
    def log_access(response: Response) -> Response:
        response.headers[REQUEST_ID_HEADER] = g.get("request_id", "")
        started = g.get("request_started")
        duration_ms = (
            (time.perf_counter() - started) * 1000 if started is not None else -1.0
        )
        # Only the path is logged: no query string, headers, or body.
        access_logger.info(
            '%s "%s %s" %s %.1fms user=%s',
            request.remote_addr,
            request.method,
            request.path,
            response.status_code,
            duration_ms,
            g.get("api_user_id", "-"),
        )
        return response

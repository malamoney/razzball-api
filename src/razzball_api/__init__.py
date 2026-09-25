"""Razzball projections REST API."""

import logging

from flask import Flask, jsonify
from flask.json.provider import DefaultJSONProvider
from flask.typing import ResponseReturnValue
from werkzeug.middleware.proxy_fix import ProxyFix

from razzball_api.audit import register_request_audit
from razzball_api.config import Settings
from razzball_api.errors import register_error_handlers
from razzball_api.extensions import db
from razzball_api.http_policy import register_http_policy
from razzball_api.observability import configure_logging, register_request_logging
from razzball_api.projections.routes import bp as projections_bp

logger = logging.getLogger(__name__)

HEALTH_ENDPOINT = "health"


def create_app(settings: Settings | None = None) -> Flask:
    """Build a configured application.

    With no argument, settings are read from ``RAZZBALL_*`` environment variables
    and a missing required value raises ``ConfigError``.
    """
    settings = settings if settings is not None else Settings.from_env()
    configure_logging(settings.log_level)

    app = Flask(__name__)
    app.config.from_mapping(settings.to_flask_config())
    app.extensions["razzball_settings"] = settings
    app.url_map.strict_slashes = False
    if isinstance(app.json, DefaultJSONProvider):
        # Keep projection columns in database order.
        app.json.sort_keys = False

    if settings.proxy_count:
        app.wsgi_app = ProxyFix(
            app.wsgi_app, x_for=settings.proxy_count, x_proto=settings.proxy_count
        )
    if not settings.trusted_hosts:
        logger.warning("RAZZBALL_TRUSTED_HOSTS is not set; Host header is not checked")

    db.init_app(app)

    exempt = frozenset({HEALTH_ENDPOINT, "static"})
    register_request_logging(app)
    register_http_policy(app, exempt_endpoints=exempt)
    if settings.log_requests_to_database:
        register_request_audit(app, exempt_endpoints=exempt)
    register_error_handlers(app)

    app.register_blueprint(projections_bp)

    @app.get("/health", endpoint=HEALTH_ENDPOINT)
    def health() -> ResponseReturnValue:
        # Liveness only: no database access and nothing about the deployment.
        return jsonify({"status": "ok"})

    return app

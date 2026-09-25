import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import text

from razzball_api.config import ConfigError, Settings
from razzball_api.extensions import db
from tests.conftest import AppFactory, auth_headers, request_log_rows

REQUIRED_ENV = {
    "RAZZBALL_BASEBALL_DATABASE_URL": "sqlite://",
    "RAZZBALL_BASKETBALL_DATABASE_URL": "sqlite://",
    "RAZZBALL_FOOTBALL_DATABASE_URL": "sqlite://",
}

# --- configuration ------------------------------------------------------------


def test_settings_from_env_defaults() -> None:
    settings = Settings.from_env(REQUIRED_ENV)

    assert settings.proxy_count == 0
    assert settings.trusted_hosts == ()
    assert settings.log_requests_to_database is True
    assert settings.timezone == "America/New_York"
    assert settings.db_connect_timeout == 5


def test_settings_from_env_parses_values() -> None:
    settings = Settings.from_env(
        REQUIRED_ENV
        | {
            "RAZZBALL_TRUSTED_HOSTS": "api.razzball.com, localhost",
            "RAZZBALL_PROXY_COUNT": "1",
            "RAZZBALL_LOG_REQUESTS_TO_DATABASE": "false",
            "RAZZBALL_LOG_LEVEL": "debug",
            "RAZZBALL_MAX_ROWS": "50",
            "RAZZBALL_DB_CONNECT_TIMEOUT": "2",
        }
    )

    assert settings.trusted_hosts == ("api.razzball.com", "localhost")
    assert settings.proxy_count == 1
    assert settings.log_requests_to_database is False
    assert settings.log_level == "DEBUG"
    assert settings.max_rows == 50
    assert settings.db_connect_timeout == 2


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({}, "RAZZBALL_BASEBALL_DATABASE_URL is required"),
        (REQUIRED_ENV | {"RAZZBALL_PROXY_COUNT": "two"}, "PROXY_COUNT"),
        (REQUIRED_ENV | {"RAZZBALL_PROXY_COUNT": "-1"}, "PROXY_COUNT"),
        (REQUIRED_ENV | {"RAZZBALL_LOG_REQUESTS_TO_DATABASE": "maybe"}, "true or"),
        (REQUIRED_ENV | {"RAZZBALL_TIMEZONE": "Mars/Olympus"}, "TIMEZONE"),
        (REQUIRED_ENV | {"RAZZBALL_LOG_LEVEL": "LOUD"}, "LOG_LEVEL"),
        (REQUIRED_ENV | {"RAZZBALL_MAX_ROWS": "0"}, "MAX_ROWS"),
        (REQUIRED_ENV | {"RAZZBALL_DB_CONNECT_TIMEOUT": "0"}, "DB_CONNECT_TIMEOUT"),
        (REQUIRED_ENV | {"RAZZBALL_DB_CONNECT_TIMEOUT": "-3"}, "DB_CONNECT_TIMEOUT"),
        (REQUIRED_ENV | {"RAZZBALL_DB_CONNECT_TIMEOUT": "fast"}, "DB_CONNECT_TIMEOUT"),
        (
            REQUIRED_ENV | {"RAZZBALL_FOOTBALL_DATABASE_URL": "not a url"},
            "RAZZBALL_FOOTBALL_DATABASE_URL is not a valid",
        ),
        (
            REQUIRED_ENV
            | {"RAZZBALL_BASKETBALL_DATABASE_URL": "mysql+pymysql://u:pw@h:port/db"},
            "RAZZBALL_BASKETBALL_DATABASE_URL is not a valid",
        ),
    ],
)
def test_invalid_settings_fail_fast(env: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings.from_env(env)


def test_config_error_does_not_echo_secret_values() -> None:
    env = REQUIRED_ENV | {"RAZZBALL_PROXY_COUNT": "s3cret-value"}
    with pytest.raises(ConfigError) as exc_info:
        Settings.from_env(env)
    assert "s3cret-value" not in str(exc_info.value)


def test_app_instances_are_independent(make_app: AppFactory) -> None:
    first = make_app(seed=False, max_rows=5)
    second = make_app(seed=False, max_rows=7)

    assert first.extensions["razzball_settings"].max_rows == 5
    assert second.extensions["razzball_settings"].max_rows == 7


# --- HTTP behaviour -----------------------------------------------------------


def test_health_needs_no_key_or_vendor_accept(app: Flask, client: FlaskClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}
    assert request_log_rows(app) == []


@pytest.mark.parametrize("accept", [None, "application/json", "*/*", "text/html"])
def test_vendor_accept_header_is_required(
    client: FlaskClient, accept: str | None
) -> None:
    headers = auth_headers()
    if accept is None:
        del headers["Accept"]
    else:
        headers["Accept"] = accept

    response = client.get("/mlb/projections/botros", headers=headers)

    assert response.status_code == 406
    assert "application/vnd.razzball" in response.get_json()["description"]


def test_both_vendor_media_types_are_accepted(client: FlaskClient) -> None:
    for accept in ("application/vnd.razzball.api", "application/vnd.razzball-v1+json"):
        headers = auth_headers() | {"Accept": accept}
        assert client.get("/mlb/projections/botros", headers=headers).status_code == 200


def test_unknown_url_is_json_404(client: FlaskClient) -> None:
    response = client.get("/nope", headers=auth_headers())

    assert response.status_code == 404
    assert response.get_json()["status_code"] == 404


def test_wrong_method_is_405_with_allow_header(client: FlaskClient) -> None:
    response = client.delete("/nba/projections/all", headers=auth_headers())

    assert response.status_code == 405
    assert "GET" in response.headers["Allow"]
    assert response.get_json()["message"] == "Method Not Allowed"


def test_untrusted_host_is_rejected(client: FlaskClient) -> None:
    response = client.get("/health", headers={"Host": "evil.example"})
    assert response.status_code == 400


def test_security_headers_are_set(client: FlaskClient) -> None:
    response = client.get("/nba/projections/all", headers=auth_headers())

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert "default-src 'none'" in response.headers["Content-Security-Policy"]
    assert "Set-Cookie" not in response.headers


def test_unexpected_exception_returns_safe_500(app: Flask) -> None:
    app.config["PROPAGATE_EXCEPTIONS"] = False

    @app.get("/boom")
    def boom() -> str:
        raise RuntimeError("password=hunter2 at db.internal:3306")

    response = app.test_client().get("/boom", headers=auth_headers())

    assert response.status_code == 500
    assert "hunter2" not in response.get_data(as_text=True)
    assert response.get_json()["message"] == "Internal Server Error"


@pytest.mark.parametrize(
    ("incoming", "echoed"),
    [("abcd1234-upstream", True), ("bad id with spaces", False), ("", False)],
)
def test_request_id_is_validated(
    client: FlaskClient, incoming: str, echoed: bool
) -> None:
    response = client.get("/health", headers={"X-Request-ID": incoming})

    request_id = response.headers["X-Request-ID"]
    assert (request_id == incoming) is echoed
    assert request_id


def test_audit_failure_does_not_break_the_response(app: Flask) -> None:
    with app.app_context(), db.engine.begin() as conn:
        conn.execute(text("DROP TABLE api_request_log"))

    response = app.test_client().get("/nba/projections/all", headers=auth_headers())

    assert response.status_code == 200


def test_audit_can_be_disabled(make_app: AppFactory) -> None:
    app = make_app(seed=False, log_requests_to_database=False)
    # No tables exist at all: any audit insert would fail and be logged.
    response = app.test_client().get("/health")
    assert response.status_code == 200
    assert "audit_request" not in {
        f.__name__ for f in app.after_request_funcs.get(None, [])
    }


def test_proxy_fix_uses_forwarded_client_address_only_when_configured(
    app: Flask, make_app: AppFactory
) -> None:
    headers = auth_headers() | {"X-Forwarded-For": "203.0.113.9"}
    app.test_client().get("/nba/projections/all", headers=headers)
    assert request_log_rows(app)[-1]["remote_addr"] == "127.0.0.1"

    proxied = make_app(proxy_count=1)
    proxied.test_client().get("/nba/projections/all", headers=headers)
    assert request_log_rows(proxied)[-1]["remote_addr"] == "203.0.113.9"


# --- database connect timeout -------------------------------------------------

MYSQL_URLS = {
    "baseball_database_url": "mysql+pymysql://api:pw@db.invalid/razzball_wp2012",
    "basketball_database_url": "mysql+pymysql://api:pw@db.invalid/razzball_basketball",
    "football_database_url": "mysql+pymysql://api:pw@db.invalid/razzball_football",
}


def connect_timeouts(app: Flask) -> dict[str | None, str | tuple[str, ...] | None]:
    with app.app_context():
        return {
            bind: engine.url.query.get("connect_timeout")
            for bind, engine in db.engines.items()
        }


def test_mysql_engines_get_default_connect_timeout(make_app: AppFactory) -> None:
    app = make_app(seed=False, **MYSQL_URLS)

    assert connect_timeouts(app) == {None: "5", "basketball": "5", "football": "5"}


def test_connect_timeout_in_url_wins_over_setting(make_app: AppFactory) -> None:
    urls = MYSQL_URLS | {
        "football_database_url": MYSQL_URLS["football_database_url"]
        + "?connect_timeout=30"
    }
    app = make_app(seed=False, db_connect_timeout=2, **urls)

    assert connect_timeouts(app) == {None: "2", "basketball": "2", "football": "30"}


def test_mariadb_url_gets_timeout_and_keeps_password(make_app: AppFactory) -> None:
    app = make_app(
        seed=False,
        baseball_database_url="mariadb+pymysql://api:p%40ss@db.invalid/razzball_wp2012",
    )

    assert connect_timeouts(app)[None] == "5"
    with app.app_context():
        assert db.engines[None].url.password == "p@ss"


def test_sqlite_urls_are_left_unchanged(app: Flask) -> None:
    with app.app_context():
        urls = {str(engine.url) for engine in db.engines.values()}

    assert urls == {"sqlite://"}

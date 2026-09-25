import logging
from collections.abc import Callable
from pathlib import Path

import pymysql
import pytest
from flask import Flask, Response
from flask.testing import FlaskClient

from razzball_api.errors import unavailable_databases
from tests.conftest import AppFactory, auth_headers, request_log_rows

NFL_WEEKLY = "/nfl/projections/weekly/2025/3"


def unreachable_url(tmp_path: Path) -> str:
    # SQLite cannot create a file inside a missing directory, so this fails
    # when a connection is opened, like an unreachable server, with no network.
    return f"sqlite:///{tmp_path}/missing-dir/unreachable.db"


def mysql_refusing_app(
    make_app: AppFactory, bind: str | None, creator: Callable[[], object]
) -> Flask:
    """An app whose ``bind`` (None is baseball) is a MySQL server run by ``creator``."""
    binds: dict[str | None, object] = {
        "basketball": "sqlite://",
        "football": "sqlite://",
    }
    overrides: dict[str, object] = {}
    if bind is None:
        url = "mysql+pymysql://api:pw@db.invalid/razzball_wp2012"
        overrides["baseball_database_url"] = url
    else:
        url = f"mysql+pymysql://api:pw@db.invalid/razzball_{bind}"
    binds[bind] = {"url": url, "creator": creator}
    return make_app(
        unseeded={bind},
        extra_flask_config={"TESTING": True, "SQLALCHEMY_BINDS": binds},
        **overrides,
    )


@pytest.fixture
def football_down(make_app: AppFactory, tmp_path: Path) -> Flask:
    return make_app(
        football_database_url=unreachable_url(tmp_path), unseeded={"football"}
    )


def test_unreachable_projections_database_is_503(football_down: Flask) -> None:
    response = football_down.test_client().get(NFL_WEEKLY, headers=auth_headers())

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    assert response.get_json() == {
        "error": True,
        "status_code": 503,
        "message": "Service Unavailable",
        "description": "The service is temporarily unavailable.",
    }


def test_other_databases_keep_working(football_down: Flask) -> None:
    client: FlaskClient = football_down.test_client()

    response = client.get("/nba/projections/all", headers=auth_headers())

    assert response.status_code == 200


def test_unreachable_key_database_is_503_not_401(
    make_app: AppFactory, tmp_path: Path
) -> None:
    app = make_app(baseball_database_url=unreachable_url(tmp_path), unseeded={None})

    response = app.test_client().get(NFL_WEEKLY, headers=auth_headers())

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"


def test_outage_is_logged_once_without_leaking_details(
    football_down: Flask, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="razzball_api"):
        response = football_down.test_client().get(NFL_WEEKLY, headers=auth_headers())

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert [r.getMessage() for r in errors] == ["football database unavailable"]
    assert all(r.exc_info is None for r in errors)
    body = response.get_data(as_text=True)
    for leaked in (str(tmp_path), "sqlite", "unable to open"):
        assert leaked not in body
        assert leaked not in caplog.text


@pytest.mark.parametrize(
    ("code", "retry_after"),
    [(2003, "30"), (1045, None), (1130, None)],
)
def test_retry_after_depends_on_mysql_error_code(
    make_app: AppFactory,
    caplog: pytest.LogCaptureFixture,
    code: int,
    retry_after: str | None,
) -> None:
    def refuse_connection() -> object:
        raise pymysql.err.OperationalError(code, "Host 'db.internal' said no")

    app = mysql_refusing_app(make_app, "football", refuse_connection)

    with caplog.at_level(logging.WARNING, logger="razzball_api"):
        response = app.test_client().get(NFL_WEEKLY, headers=auth_headers())

    assert response.status_code == 503
    assert response.headers.get("Retry-After") == retry_after
    assert f"football database unavailable (driver error code {code})" in caplog.text
    assert "db.internal" not in caplog.text + response.get_data(as_text=True)


def test_later_request_steps_can_see_the_outage(football_down: Flask) -> None:
    seen: list[frozenset[str]] = []

    @football_down.after_request
    def capture(response: Response) -> Response:
        seen.append(unavailable_databases())
        return response

    client = football_down.test_client()
    client.get(NFL_WEEKLY, headers=auth_headers())
    client.get("/nba/projections/all", headers=auth_headers())

    # Recorded for the failing request only; the next request starts clean.
    assert seen == [frozenset({"football"}), frozenset()]


def test_key_database_outage_never_logs_the_host(
    make_app: AppFactory, caplog: pytest.LogCaptureFixture
) -> None:
    # PyMySQL's message names the host, so it must never reach the logs.
    def refuse_connection() -> object:
        raise pymysql.err.OperationalError(
            2003, "Can't connect to MySQL server on 'db.internal' (timed out)"
        )

    app = mysql_refusing_app(make_app, None, refuse_connection)

    with caplog.at_level(logging.WARNING, logger="razzball_api"):
        response = app.test_client().get(NFL_WEEKLY, headers=auth_headers())

    assert response.status_code == 503
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors
    assert all(r.exc_info is None for r in errors)
    assert all("baseball database unavailable" in r.getMessage() for r in errors)
    assert "db.internal" not in caplog.text


def test_audit_is_skipped_when_key_database_is_down(
    make_app: AppFactory, caplog: pytest.LogCaptureFixture
) -> None:
    attempts: list[int] = []

    def refuse_connection() -> object:
        attempts.append(1)
        raise pymysql.err.OperationalError(2003, "Can't connect to 'db.internal'")

    app = mysql_refusing_app(make_app, None, refuse_connection)
    client = app.test_client()

    with caplog.at_level(logging.WARNING, logger="razzball_api.audit"):
        for _ in range(2):
            assert client.get(NFL_WEEKLY, headers=auth_headers()).status_code == 503

    # One connection attempt per request: the audit step does not try again.
    assert len(attempts) == 2
    audit_records = [r for r in caplog.records if r.name == "razzball_api.audit"]
    assert [r.getMessage() for r in audit_records] == [
        "api_request_log row skipped: baseball database unavailable"
    ] * 2
    assert all(r.exc_info is None for r in audit_records)


def test_audit_is_written_when_only_another_database_is_down(
    football_down: Flask,
) -> None:
    football_down.test_client().get(NFL_WEEKLY, headers=auth_headers())

    [row] = request_log_rows(football_down)
    assert row["status_code"] == 503
    assert row["uri"] == NFL_WEEKLY

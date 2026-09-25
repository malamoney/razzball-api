from collections.abc import Callable, Iterator

import pytest
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import Engine, text

from razzball_api import create_app
from razzball_api.config import Settings
from razzball_api.extensions import db

VENDOR_ACCEPT = "application/vnd.razzball-v1+json"
FULL_KEY = "key-full-access-0001"
MLB_ONLY_KEY = "key-mlb-daily-only-0002"
INACTIVE_KEY = "key-inactive-0003"

ALL_ENDPOINTS = [
    "/mlb/projections/daily",
    "/mlb/projections/botdaily",
    "/mlb/projections/botweekly",
    "/mlb/projections/botros",
    "/nba/projections/daily",
    "/nba/projections/all",
    "/nba/projections/ros",
    "/nfl/projections/weekly",
]

BASEBALL_DDL = [
    "CREATE TABLE api_user_status (id INTEGER PRIMARY KEY, status TEXT NOT NULL)",
    """CREATE TABLE api_user (
        id INTEGER PRIMARY KEY, username TEXT, api_key TEXT UNIQUE NOT NULL,
        status_id INTEGER NOT NULL, access_group_id INTEGER NOT NULL)""",
    """CREATE TABLE api_access_group_endpoint_map (
        access_group_id INTEGER NOT NULL, uri TEXT NOT NULL, method TEXT NOT NULL)""",
    """CREATE TABLE api_request_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, api_user_id INTEGER NOT NULL,
        api_key TEXT, uri TEXT, method TEXT, remote_addr TEXT,
        status_code INTEGER, description TEXT)""",
    "CREATE TABLE APISOURCE_MLB (Name TEXT, Date TEXT, HR REAL)",
    "CREATE TABLE APISOURCE_MLB_DAILY (Name TEXT, HR REAL)",
    "CREATE TABLE APISOURCE_MLB_WEEKLY (Name TEXT, HR REAL)",
    "CREATE TABLE APISOURCE_MLB_ROS (Name TEXT, HR REAL)",
]
BASKETBALL_DDL = [
    "CREATE TABLE nba_api_master (Name TEXT, Date TEXT, PTS REAL)",
    "CREATE TABLE nba_api_master_ros (Name TEXT, PTS REAL)",
]
FOOTBALL_DDL = [
    "CREATE TABLE nfl_api_master (Name TEXT, Season INTEGER, Week INTEGER, Pts REAL)",
]


def _seed(engine: Engine, statements: list[str]) -> None:
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def _seed_baseball(engine: Engine) -> None:
    _seed(engine, BASEBALL_DDL)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO api_user_status VALUES (1, 'ACTIVE'), (2, 'SUSPENDED')")
        )
        conn.execute(
            text("INSERT INTO api_user VALUES (:id, :name, :key, :status, :group)"),
            [
                {"id": 11, "name": "full", "key": FULL_KEY, "status": 1, "group": 1},
                {"id": 12, "name": "mlb", "key": MLB_ONLY_KEY, "status": 1, "group": 2},
                {"id": 13, "name": "off", "key": INACTIVE_KEY, "status": 2, "group": 1},
            ],
        )
        conn.execute(
            text("INSERT INTO api_access_group_endpoint_map VALUES (:g, :uri, 'GET')"),
            [{"g": 1, "uri": uri} for uri in ALL_ENDPOINTS]
            + [{"g": 2, "uri": "/mlb/projections/daily"}],
        )
        conn.execute(
            text(
                "INSERT INTO APISOURCE_MLB VALUES "
                "('Aaron Judge', '2026-06-01', 0.41), ('Juan Soto', '2026-06-01', NULL),"
                " ('Mookie Betts', '2026-06-02', 0.2)"
            )
        )
        for table, name in [
            ("APISOURCE_MLB_DAILY", "daily-player"),
            ("APISOURCE_MLB_WEEKLY", "weekly-player"),
            ("APISOURCE_MLB_ROS", "ros-player"),
        ]:
            conn.execute(
                text(f"INSERT INTO {table} VALUES (:name, 1.5)"), {"name": name}
            )


def _seed_basketball(engine: Engine) -> None:
    _seed(engine, BASKETBALL_DDL)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO nba_api_master VALUES "
                "('Nikola Jokic', '2026-01-10', 27.5), ('Luka Doncic', '2026-01-11', 30)"
            )
        )
        conn.execute(text("INSERT INTO nba_api_master_ros VALUES ('Victor W.', 24)"))


def _seed_football(engine: Engine) -> None:
    _seed(engine, FOOTBALL_DDL)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO nfl_api_master VALUES "
                "('Josh Allen', 2025, 3, 24.1), ('Lamar Jackson', 2025, 4, 22.0)"
            )
        )


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "baseball_database_url": "sqlite://",
        "basketball_database_url": "sqlite://",
        "football_database_url": "sqlite://",
        "trusted_hosts": ("localhost",),
        "extra_flask_config": {"TESTING": True},
    }
    values.update(overrides)
    return Settings(**values)  # pyright: ignore[reportArgumentType]


type AppFactory = Callable[..., Flask]


def seed_databases(app: Flask) -> None:
    with app.app_context():
        _seed_baseball(db.engines[None])
        _seed_basketball(db.engines["basketball"])
        _seed_football(db.engines["football"])


@pytest.fixture
def make_app() -> Iterator[AppFactory]:
    """Build seeded apps from Settings overrides; dispose their engines afterwards."""
    created: list[Flask] = []

    def factory(*, seed: bool = True, **overrides: object) -> Flask:
        app = create_app(make_settings(**overrides))
        created.append(app)
        if seed:
            seed_databases(app)
        return app

    yield factory
    for app in created:
        with app.app_context():
            for engine in db.engines.values():
                engine.dispose()


@pytest.fixture
def app(make_app: AppFactory) -> Flask:
    return make_app()


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def auth_headers(key: str | None = FULL_KEY) -> dict[str, str]:
    headers = {"Accept": VENDOR_ACCEPT}
    if key is not None:
        headers["Razzball-Api-Key"] = key
    return headers


def request_log_rows(app: Flask) -> list[dict[str, object]]:
    with app.app_context(), db.engine.connect() as conn:
        result = conn.execute(text("SELECT * FROM api_request_log ORDER BY id"))
        return [dict(row._mapping) for row in result]  # pyright: ignore[reportPrivateUsage]
